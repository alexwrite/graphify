from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import networkx as nx

from graphify.serve import _resolve_single_node


DEFAULT_AFFECTED_RELATIONS = (
    "calls",
    "indirect_call",
    "references",
    "imports",
    "imports_from",
    # `import('…')` — emitted by the Svelte/Astro/Vue rescue passes and (since
    # #2575) by plain JS/TS too. Omitting it made every dynamic import
    # invisible to blast-radius traversal even where the edge WAS in the
    # graph, and dynamic import is precisely how codebases break require
    # cycles, so the missing edges sat under the most load-bearing modules.
    "dynamic_import",
    "re_exports",
    "inherits",
    "extends",
    "implements",
    "uses",
    "mixes_in",
    "embeds",
    # `defines` — emitted when a file declares a named symbol (class, function,
    # type, etc.). Without it, `affected "<Symbol>"` is blind in languages
    # where the only incoming edge to a symbol is its `defines` edge from the
    # source file — Dart/Flutter, Python, Go, etc. — because every other
    # relation (calls, references, imports) targets the *file* node, not the
    # symbol node. The reverse walk follows `defines` from the symbol back to
    # its defining file and then continues through `imports` to consumers.
    "defines",
    "requires",
)


@dataclass(frozen=True)
class AffectedHit:
    node_id: str
    depth: int
    via_relation: str
    # The traversed edge's location — the actual call/import/reference SITE in
    # this node's file, not the node's own definition line (#BUG1). Defaults keep
    # existing constructors/tests working; None falls back to the node's def line.
    via_file: "str | None" = None
    via_location: "str | None" = None


def _node_label(graph: nx.Graph, node_id: str) -> str:
    data = graph.nodes[node_id]
    return str(data.get("label") or node_id)


def _format_location(data: dict) -> str:
    source_file = data.get("source_file") or "-"
    source_location = data.get("source_location")
    if source_location:
        return f"{source_file}:{source_location}"
    return str(source_file)


def _as_repo_relative(query: str, root: Path | None = None) -> str:
    """Repo-relative form of a path query, for matching a stored `source_file`.

    The graph stores repo-relative paths, so `./src/x.py` and
    `/abs/repo/src/x.py` name the same file as `src/x.py` and yet matched
    nothing. `affected` then printed an empty list and exited 0 — a blast-radius
    tool answering "nothing depends on this" about a file with sixteen
    dependents, and indistinguishable from a genuine zero or a typo.

    An absolute path is anchored to `root` when given — the repo root derived
    from the graph's own location — so a seed resolves regardless of the caller's
    working directory (#2706: an absolute-path seed previously only matched when
    cwd happened to be the analysed repo root, which no editor or script can
    guarantee). `root` falls back to the current directory to preserve the prior
    behaviour when a caller has no graph location to derive it from.

    Non-path queries pass through unchanged: `Path("myFunc()").as_posix()` is
    `"myFunc()"`, so label resolution is untouched. An absolute path rooted
    outside `root` is left alone — no basename guessing.
    """
    path = Path(query)
    if path.is_absolute():
        anchor = root if root is not None else Path.cwd()
        try:
            return path.relative_to(anchor).as_posix()
        except ValueError:
            # Rooted outside the repo: nothing here can make it repo-relative,
            # so leave it alone rather than guess at a basename that would match
            # some unrelated file with the same name.
            return query
    return path.as_posix()


def resolve_seed_node(
    graph: nx.Graph, query: str, root: Path | None = None
) -> tuple[str | None, str | None]:
    """Resolve *query* as `explain` does, after making a `./` or absolute path repo-relative (#2706)."""
    if query.startswith(("./", ".\\")) or Path(query).is_absolute():
        query = _as_repo_relative(query, root)
    return _resolve_single_node(graph, query)


def resolve_seed(graph: nx.Graph, query: str, root: Path | None = None) -> str | None:
    return resolve_seed_node(graph, query, root)[0]


def affected_nodes(
    graph: nx.Graph,
    seed: str,
    *,
    relations: Iterable[str] = DEFAULT_AFFECTED_RELATIONS,
    depth: int = 2,
) -> list[AffectedHit]:
    relation_set = set(relations)
    seen = {seed}
    queue: deque[tuple[str, int]] = deque([(seed, 0)])
    hits: list[AffectedHit] = []

    # #1669: seed the reverse walk with the root's own member nodes (one outward
    # `method`/`contains` hop). A caller can bind to a class's method node rather
    # than the class node itself (e.g. `Service.call` resolves to the `def
    # self.call` node, #1634), so those callers are unreachable from the class
    # otherwise. The member nodes are seeds only (not reported as hits), and
    # `method`/`contains` stay out of the general relation-filtered walk, so this
    # adds no forward noise anywhere else.
    if hasattr(graph, "out_edges"):
        member_edges = graph.out_edges(seed, data=True)
    else:
        member_edges = (
            (s, t, d) for s, t, d in graph.edges(data=True) if s == seed
        )
    for _s, member, data in member_edges:
        if str(data.get("relation", "")) not in ("method", "contains"):
            continue
        member = str(member)
        if member not in seen:
            seen.add(member)
            queue.append((member, 0))

    while queue:
        current, current_depth = queue.popleft()
        if current_depth >= depth:
            continue
        if hasattr(graph, "in_edges"):
            incoming = graph.in_edges(current, data=True)
        else:
            incoming = (
                (source, target, data)
                for source, target, data in graph.edges(data=True)
                if target == current
            )
        for source, _target, data in incoming:
            relation = str(data.get("relation", ""))
            if relation not in relation_set:
                continue
            source = str(source)
            if source in seen:
                continue
            seen.add(source)
            # Carry the matched edge's location (taken from the SAME edge dict
            # whose relation passed the filter, so relation and location stay
            # consistent) — that is the call/import/reference site in `source`'s
            # own file, which is where the user should click (#BUG1).
            hit = AffectedHit(
                source, current_depth + 1, relation,
                via_file=str(data.get("source_file") or "") or None,
                via_location=str(data.get("source_location") or "") or None,
            )
            hits.append(hit)
            queue.append((source, current_depth + 1))

    return hits


def format_affected(
    graph: nx.Graph,
    query: str,
    *,
    relations: Iterable[str] = DEFAULT_AFFECTED_RELATIONS,
    depth: int = 2,
    root: Path | None = None,
) -> str:
    seed, refusal = resolve_seed_node(graph, query, root)
    if seed is None:
        return str(refusal)
    return render_affected(graph, seed, relations=relations, depth=depth)


def render_affected(
    graph: nx.Graph,
    seed: str,
    *,
    relations: Iterable[str] = DEFAULT_AFFECTED_RELATIONS,
    depth: int = 2,
) -> str:
    relation_list = tuple(relations)
    hits = affected_nodes(graph, seed, relations=relation_list, depth=depth)
    lines = [
        f"Affected nodes for {_node_label(graph, seed)}",
        f"Relations: {', '.join(relation_list)}",
        f"Depth: {depth}",
    ]
    if not hits:
        lines.append("No affected nodes found.")
        return "\n".join(lines)

    for hit in hits:
        data = graph.nodes[hit.node_id]
        if hit.via_location:
            # The relation SITE in this node's file (call/import/reference line),
            # labeled by [via_relation] so it's never mistaken for a def line.
            location = f"{hit.via_file or data.get('source_file') or '-'}:{hit.via_location}"
        else:
            location = _format_location(data)  # honest fallback: the node's own def line
        lines.append(
            f"- {_node_label(graph, hit.node_id)} [{hit.via_relation}] {location}"
        )
    return "\n".join(lines)


def load_graph(path: Path) -> nx.Graph:
    import json
    from networkx.readwrite import json_graph

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            f"Cannot read graph file {path}: {exc}. "
            "Re-run 'graphify extract' to regenerate it."
        ) from exc
    # Force directed so stored caller→callee direction survives the round-trip;
    # mirrors serve.py and __main__.py (#1174).
    raw = {**raw, "directed": True}
    # Normalize the edge key: graphify's `extract` output uses "edges" while
    # networkx's node_link_data default is "links". Without this, an edges-keyed
    # graph.json raises an uncaught KeyError: 'links' here — every other loader
    # (__main__.py) already normalizes this (#738; same class as #1198).
    if "links" not in raw and "edges" in raw:
        raw = dict(raw, links=raw["edges"])
    try:
        return json_graph.node_link_graph(raw, edges="links")
    except TypeError:
        return json_graph.node_link_graph(raw)
