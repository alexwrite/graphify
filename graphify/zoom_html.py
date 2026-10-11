"""zoom_html - a three-level architecture view of graph.json (``graphify export zoom-html``).

Level 1 shows the repository's domains, level 2 one domain with its classes laid
out in columns by layer, level 3 one class with its methods, their callers and
callees. Every judgement the page shows (domain, layer, call against the layer
order) is computed here, in Python, so it is testable; the page only draws it.

Heuristic boundaries, all fail-closed:
  * A class is a node carrying ``_callable_class``; a method is the target of a
    ``method`` edge whose source is a class.
  * Layers come from folder names only (Symfony by type or by domain, Laravel).
    A class whose folder names no layer goes to ``other``, never guessed.
  * The domain is the first folder under the code root; inside a layer folder
    (``src/Controller/Account/``) it is the folder below the layer. A class
    placed directly in a layer folder has no known domain.
  * A template belongs to a domain only through a ``renders`` edge from one of
    that domain's classes.
  * Only EXTRACTED calls can mark a dependency as going against the layer or
    domain order; INFERRED calls are counted and drawn, never accused.
"""

from __future__ import annotations

import json
import re
from html import escape
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from graphify.paths import restore_arc_direction

LAYERS: tuple[tuple[str, str], ...] = (
    ("route", "Route"),
    ("controller", "Controller"),
    ("service", "Service"),
    ("repository", "Repository"),
    ("entity", "Entity / Model"),
    ("view", "View"),
    ("other", "Other"),
)
LAYER_INDEX = {key: i for i, (key, _) in enumerate(LAYERS)}
# Allowed call direction: a layer may call the ones after it, never the ones before.
LAYER_RANK = {"route": 0, "controller": 1, "service": 2, "repository": 3, "entity": 4}

_LAYER_DIRS = {
    "route": "route", "routes": "route", "routing": "route",
    "controller": "controller", "controllers": "controller",
    "service": "service", "services": "service",
    "repository": "repository", "repositories": "repository",
    "entity": "entity", "entities": "entity", "model": "entity", "models": "entity",
    "view": "view", "views": "view",
}
_TEST_DIRS = {"test", "tests", "spec", "specs", "__tests__"}
_TEST_FILE = re.compile(r"(Test|Tests|_test|\.test|\.spec)\.[A-Za-z]+$|^test_[^/]*\.py$")
_VENDOR_DIRS = {"vendor", "vendors", "node_modules", "bower_components", "third_party", "third-party"}
_CALL_RELATIONS = {"calls", "indirect_call"}
_TEMPLATE_SUFFIXES = (".twig", ".blade.php", ".html", ".htm", ".jinja", ".jinja2", ".j2", ".vue", ".svelte")

KIND_DOMAIN = "domain"
KIND_NONE = "none"
KIND_TESTS = "tests"
KIND_VENDOR = "vendor"
KIND_OUTSIDE = "outside"


def _parts(source_file: str) -> list[str]:
    return [p for p in source_file.replace("\\", "/").split("/") if p and p != "."]


def is_test_path(source_file: str) -> bool:
    parts = _parts(source_file)
    if any(p.lower() in _TEST_DIRS for p in parts[:-1]):
        return True
    return bool(parts) and bool(_TEST_FILE.search(parts[-1]))


def is_vendor_path(source_file: str) -> bool:
    return any(p.lower() in _VENDOR_DIRS for p in _parts(source_file)[:-1])


def classify_layer(source_file: str, root: Optional[str]) -> str:
    """Layer of a class from the folders between the code root and the file."""
    parts = _parts(source_file)
    dirs = parts[1:-1] if root and parts and parts[0] == root else parts[:-1]
    for d in dirs:
        layer = _LAYER_DIRS.get(d.lower())
        if layer:
            return layer
    return "other"


def classify_domain(source_file: str, root: Optional[str]) -> Optional[str]:
    """Domain folder of a class under *root*, or None when no folder names one."""
    parts = _parts(source_file)
    if not root or len(parts) < 3 or parts[0] != root:
        return None
    rest = parts[1:-1]
    first = rest[0]
    if first.lower() == "http" and len(rest) > 1 and rest[1].lower() in ("controllers", "controller"):
        rest = rest[1:]
        first = rest[0]
    if first.lower() in _LAYER_DIRS:
        return rest[1] if len(rest) > 1 else None
    return first


def _line(location: Any) -> int:
    m = re.match(r"L(\d+)", str(location or ""))
    return int(m.group(1)) if m else 0


def _group_of(source_file: str, external: bool, root: Optional[str]) -> tuple[str, str]:
    """(kind, name) of the level-1 box a class belongs to."""
    if external or not source_file or is_vendor_path(source_file):
        return KIND_VENDOR, "Framework / vendor"
    if is_test_path(source_file):
        return KIND_TESTS, "Tests"
    parts = _parts(source_file)
    if not root or not parts or parts[0] != root:
        return KIND_OUTSIDE, f"{parts[0]}/" if len(parts) > 1 else "(repository root)"
    domain = classify_domain(source_file, root)
    if domain is None:
        return KIND_NONE, "(no domain)"
    return KIND_DOMAIN, domain


def detect_code_root(class_files: list[str]) -> Optional[str]:
    """Top folder holding the most application classes (tests and vendor excluded)."""
    counts = Counter(
        _parts(f)[0] for f in class_files
        if f and len(_parts(f)) > 1 and not is_test_path(f) and not is_vendor_path(f)
    )
    if not counts:
        return None
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def order_domains(names: list[str], weights: dict[tuple[int, int], int]) -> list[int]:
    """Greedy feedback-arc-set order (Eades, Lin, Smyth 1993), callers first, ties broken by name."""
    alive = set(range(len(names)))
    out_w: dict[int, dict[int, int]] = defaultdict(dict)
    in_w: dict[int, dict[int, int]] = defaultdict(dict)
    for (s, t), w in weights.items():
        if s != t and w > 0:
            out_w[s][t] = w
            in_w[t][s] = w
    head: list[int] = []
    tail: list[int] = []

    def live(m: dict[int, int]) -> int:
        return sum(w for n, w in m.items() if n in alive)

    while alive:
        changed = True
        while changed:
            changed = False
            for n in sorted(alive, key=lambda i: names[i]):
                if live(out_w[n]) == 0:
                    tail.insert(0, n); alive.discard(n); changed = True
            for n in sorted(alive, key=lambda i: names[i]):
                if live(in_w[n]) == 0:
                    head.append(n); alive.discard(n); changed = True
        if alive:
            best = max(sorted(alive, key=lambda i: names[i]), key=lambda i: live(out_w[i]) - live(in_w[i]))
            head.append(best)
            alive.discard(best)
    return head + tail


def _rows(order: list[int], forward: dict[int, set[int]]) -> dict[int, int]:
    """Longest-path layering over forward edges: a domain sits below everything that calls it."""
    row = {n: 0 for n in order}
    for n in order:
        for t in forward.get(n, ()):
            row[t] = max(row[t], row[n] + 1)
    return row


def build_zoom_data(graph: dict, project: str = "") -> dict:
    """Compute the three zoom levels from a node-link graph dict (pure, no I/O)."""
    if "links" not in graph and "edges" in graph:
        graph = dict(graph, links=graph["edges"])
    data = restore_arc_direction(graph)
    raw_links = data.get("links")
    links: list = raw_links if isinstance(raw_links, list) else []
    nodes = {str(n["id"]): n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}

    class_ids = sorted(i for i, n in nodes.items() if n.get("_callable_class"))
    class_idx = {cid: k for k, cid in enumerate(class_ids)}
    root = detect_code_root([str(nodes[c].get("source_file") or "") for c in class_ids])

    files: list[str] = []
    file_idx: dict[str, int] = {}

    def fid(path: str) -> int:
        if path not in file_idx:
            file_idx[path] = len(files)
            files.append(path)
        return file_idx[path]

    groups: list[tuple[str, str]] = []
    group_idx: dict[tuple[str, str], int] = {}
    classes: list[list] = []
    for cid in class_ids:
        n = nodes[cid]
        src = str(n.get("source_file") or "")
        key = _group_of(src, bool(n.get("external")), root)
        if key not in group_idx:
            group_idx[key] = len(groups)
            groups.append(key)
        layer = classify_layer(src, root) if key[0] in (KIND_DOMAIN, KIND_NONE) else "other"
        classes.append([str(n.get("label") or cid), group_idx[key], LAYER_INDEX[layer],
                        fid(src), _line(n.get("source_location")), 0])

    methods: list[list] = []
    method_of: dict[str, tuple[int, int]] = {}
    for link in links:
        if not isinstance(link, dict) or link.get("relation") != "method":
            continue
        owner, member = str(link.get("_src")), str(link.get("_tgt"))
        if owner in class_idx and member in nodes and member not in method_of and member not in class_idx:
            m = nodes[member]
            method_of[member] = (class_idx[owner], len(methods))
            methods.append([class_idx[owner], str(m.get("label") or member), _line(m.get("source_location"))])
            classes[class_idx[owner]][5] += 1

    def owner_of(node_id: str) -> tuple[int, int]:
        if node_id in class_idx:
            return class_idx[node_id], -1
        return method_of.get(node_id, (-1, -1))

    calls: list[list] = []
    pair: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0])
    templates: list[list] = []
    template_idx: dict[str, int] = {}
    renders: Counter = Counter()
    for link in links:
        if not isinstance(link, dict):
            continue
        rel = link.get("relation")
        s_cls, s_m = owner_of(str(link.get("_src")))
        if s_cls < 0:
            continue
        tgt = str(link.get("_tgt"))
        if rel == "renders":
            t = nodes.get(tgt)
            tfile = str((t or {}).get("source_file") or "")
            if t is not None and tfile.lower().endswith(_TEMPLATE_SUFFIXES):
                if tgt not in template_idx:
                    template_idx[tgt] = len(templates)
                    templates.append([str(t.get("label") or tgt), fid(tfile)])
                renders[(s_cls, template_idx[tgt])] += 1
            continue
        if rel not in _CALL_RELATIONS:
            continue
        t_cls, t_m = owner_of(tgt)
        if t_cls < 0:
            continue
        extracted = 1 if str(link.get("confidence") or "EXTRACTED").upper() == "EXTRACTED" else 0
        calls.append([s_cls, s_m, t_cls, t_m, fid(str(link.get("source_file") or "")),
                      _line(link.get("source_location")), extracted])
        if s_cls != t_cls:
            pair[(s_cls, t_cls)][0 if extracted else 1] += 1

    class_links = []
    for (s, t), (ext, inf) in sorted(pair.items()):
        rs = LAYER_RANK.get(LAYERS[classes[s][2]][0])
        rt = LAYER_RANK.get(LAYERS[classes[t][2]][0])
        against = 1 if ext and rs is not None and rt is not None and rs > rt else 0
        class_links.append([s, t, ext, inf, against])

    dom_pair: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0])
    for s, t, ext, inf, _ in class_links:
        gs, gt = classes[s][1], classes[t][1]
        if gs != gt:
            dom_pair[(gs, gt)][0] += ext
            dom_pair[(gs, gt)][1] += inf
    names = [f"{k}:{n}" for k, n in groups]
    order = order_domains(names, {k: v[0] for k, v in dom_pair.items()})
    position = {g: i for i, g in enumerate(order)}
    forward: dict[int, set[int]] = defaultdict(set)
    domain_links = []
    for (s, t), (ext, inf) in sorted(dom_pair.items()):
        against = 1 if ext and position[t] < position[s] else 0
        if ext and not against:
            forward[s].add(t)
        domain_links.append([s, t, ext, inf, against])
    row = _rows(order, forward)
    sizes = Counter(c[1] for c in classes)
    domains = [{"name": n, "kind": k, "classes": sizes[i], "order": position[i], "row": row[i]}
               for i, (k, n) in enumerate(groups)]

    return {
        "project": project,
        "root": root,
        "layers": [label for _, label in LAYERS],
        "layerRank": [LAYER_RANK.get(key, -1) for key, _ in LAYERS],
        "files": files,
        "domains": domains,
        "domainLinks": domain_links,
        "classes": classes,
        "classLinks": class_links,
        "methods": methods,
        "calls": calls,
        "templates": templates,
        "renders": [[c, t, n] for (c, t), n in sorted(renders.items())],
    }


def _embed(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def emit_html(payload: dict) -> str:
    title = escape(f"{payload.get('project') or 'graphify'} - architecture zoom")
    return _TEMPLATE.replace("__TITLE__", title).replace("__DATA__", _embed(payload))


def write_zoom_html(graph_path: Path, output_path: Path, *, project_label: Optional[str] = None) -> Path:
    """Read graph.json, compute the three levels and write one self-contained HTML file."""
    from graphify.security import check_graph_file_size_cap
    check_graph_file_size_cap(graph_path)
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    if project_label is None:
        parent = graph_path.resolve().parent
        project_label = (parent.parent if parent.name == "graphify-out" else parent).name
    payload = build_zoom_data(graph, project=project_label)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(emit_html(payload), encoding="utf-8")
    return output_path


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  color-scheme: light;
  --bg: #fcfcfb; --panel: #f3f2ee; --box: #ffffff; --border: #76756f;
  --ink: #0b0b0b; --ink2: #4a4945; --edge: #6b6a64; --bar: #2a78d6;
  --danger: #c22d2d; --on-danger: #ffffff; --focus: #0b0b0b;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #1a1a19; --panel: #222220; --box: #262624; --border: #8f8e87;
    --ink: #f0efec; --ink2: #c3c2b7; --edge: #9f9e97; --bar: #5598e7;
    --danger: #f07c7c; --on-danger: #1a1a19; --focus: #f0efec;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #1a1a19; --panel: #222220; --box: #262624; --border: #8f8e87;
  --ink: #f0efec; --ink2: #c3c2b7; --edge: #9f9e97; --bar: #5598e7;
  --danger: #f07c7c; --on-danger: #1a1a19; --focus: #f0efec;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
a { color: inherit; }
:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
[tabindex="-1"]:focus { outline: none; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
header { padding: 16px 20px 12px; border-bottom: 1px solid var(--border); display: grid; gap: 10px; }
h1 { font-size: 1.15rem; margin: 0; font-weight: 650; }
h1 span { color: var(--ink2); font-weight: 400; }
.crumbs ol { list-style: none; display: flex; flex-wrap: wrap; gap: 4px; margin: 0; padding: 0; font-size: .95rem; }
.crumbs li + li::before { content: "/"; color: var(--ink2); margin-right: 4px; }
.crumbs a { padding: 4px 2px; }
.crumbs [aria-current] { font-weight: 650; text-decoration: none; }
.toolbar { display: flex; flex-wrap: wrap; gap: 6px 18px; border: 0; margin: 0; padding: 0; font-size: .9rem; color: var(--ink2); }
.toolbar legend { float: left; margin-right: 8px; font-weight: 600; color: var(--ink); }
.toolbar label { display: inline-flex; align-items: center; gap: 6px; min-height: 32px; cursor: pointer; }
.toolbar input { width: 18px; height: 18px; accent-color: var(--bar); margin: 0; }
.layout { display: grid; grid-template-columns: minmax(0, 1fr) 260px; gap: 0; }
main { padding: 16px 20px 40px; min-width: 0; }
main:focus { outline: none; }
h2 { font-size: 1.3rem; margin: 0 0 4px; overflow-wrap: anywhere; }
h2 small { font-size: .9rem; font-weight: 400; color: var(--ink2); margin-left: 8px; }
.lede { margin: 0 0 14px; color: var(--ink2); font-size: .95rem; }
.legend { border-left: 1px solid var(--border); padding: 16px 18px; font-size: .88rem; color: var(--ink2); background: var(--panel); }
.legend h2 { font-size: .95rem; color: var(--ink); margin-bottom: 10px; }
.legend ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }
.legend li { display: grid; grid-template-columns: 44px 1fr; gap: 10px; align-items: center; }
.legend svg { display: block; }
.details { min-height: 3em; margin: 0 0 12px; padding: 8px 12px; background: var(--panel); border-radius: 6px; font-size: .92rem; color: var(--ink2); }
.details strong { color: var(--ink); }
.canvas { overflow-x: auto; }
svg text { fill: var(--ink); font: 13px system-ui, -apple-system, "Segoe UI", sans-serif; }
svg .sub { fill: var(--ink2); font-size: 12px; }
svg .name { font-weight: 650; font-size: 14px; }
.node rect.frame { fill: var(--box); stroke: var(--border); stroke-width: 1; }
.node rect.track { fill: var(--panel); }
.node rect.fill { fill: var(--bar); }
.node.fold rect.frame { stroke-dasharray: 4 3; }
.node:focus { outline: none; }
.node:focus-visible rect.frame, .node.is-active rect.frame { stroke: var(--focus); stroke-width: 3; }
.node.is-partner rect.frame { stroke: var(--ink); stroke-width: 2; }
.badge circle, .badge rect { fill: var(--danger); }
.badge text { fill: var(--on-danger); font-weight: 700; font-size: 12px; }
.link path { fill: none; stroke: var(--edge); }
.link.inferred path { stroke-dasharray: 2 4; }
.link.against path { stroke: var(--danger); }
.link .pill { fill: var(--bg); stroke: var(--edge); stroke-width: 1; }
.link.against .pill { fill: var(--danger); stroke: var(--danger); }
.link.against text { fill: var(--on-danger); font-weight: 700; }
.link text { font-size: 12px; }
.has-active .link:not(.is-related) { opacity: .12; }
.has-active .link.is-hidden.is-related { display: inline; }
.link.is-hidden { display: none; }
marker path { fill: context-stroke; }
#arrow path { fill: var(--edge); } #arrow-against path { fill: var(--danger); }
.columns { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; align-items: start; }
.col { background: var(--panel); border-radius: 6px; padding: 10px; min-width: 0; }
.col h3 { margin: 0 0 8px; font-size: .95rem; display: flex; justify-content: space-between; gap: 6px; }
.col h3 span { color: var(--ink2); font-weight: 400; }
.col ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.cls { display: flex; align-items: center; gap: 6px; min-height: 36px; padding: 6px 8px; background: var(--box); border: 1px solid var(--border); border-radius: 4px; text-decoration: none; font-size: .9rem; overflow-wrap: anywhere; }
a.cls:hover { border-color: var(--ink); }
.cls.against { border: 2px solid var(--danger); }
.cls.is-partner { border: 2px solid var(--ink); }
.cls.is-active { border: 3px solid var(--focus); }
.tpl { color: var(--ink2); font-style: italic; }
.icon-against { flex: none; width: 16px; height: 16px; }
.icon-against path { fill: var(--danger); } .icon-against text { fill: var(--on-danger); font: 700 11px system-ui, sans-serif; }
.more { font: inherit; font-size: .88rem; min-height: 36px; padding: 4px 10px; background: transparent; color: var(--ink); border: 1px solid var(--border); border-radius: 4px; cursor: pointer; }
.more:hover { border-color: var(--ink); }
.flows { display: block; margin: 0 0 4px; }
.neighbours { font-size: .9rem; color: var(--ink2); margin: 0 0 14px; display: grid; gap: 4px; }
.neighbours a { color: var(--ink); }
.loc { font-family: ui-monospace, "SF Mono", Consolas, monospace; font-size: .82rem; color: var(--ink2); overflow-wrap: anywhere; }
.methods { list-style: none; padding: 0; margin: 0; display: grid; gap: 12px; }
.method { background: var(--panel); border-radius: 6px; padding: 10px 12px; }
.mhead { display: grid; grid-template-columns: minmax(0, 1fr) 140px; gap: 4px 12px; align-items: center; }
.mhead code { font-weight: 650; font-size: .95rem; overflow-wrap: anywhere; }
.mbar { height: 8px; background: var(--box); border-radius: 0 4px 4px 0; display: flex; }
.mbar i { display: block; height: 8px; background: var(--bar); border-radius: 0 4px 4px 0; }
.io { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 8px; }
.io h4 { margin: 0 0 4px; font-size: .85rem; color: var(--ink2); font-weight: 600; }
.io ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 4px; font-size: .88rem; }
.io li { display: flex; flex-wrap: wrap; gap: 2px 8px; align-items: baseline; min-width: 0; }
.io li a, .io li span { overflow-wrap: anywhere; min-width: 0; }
.tag { font-size: .78rem; border: 1px solid var(--border); border-radius: 3px; padding: 0 4px; color: var(--ink2); }
.tag.against { border-color: var(--danger); color: var(--danger); font-weight: 650; }
.empty { color: var(--ink2); }
details.table-view { margin-top: 18px; }
details.table-view summary { cursor: pointer; min-height: 32px; font-weight: 600; }
.tablewrap { overflow-x: auto; }
table { border-collapse: collapse; font-size: .88rem; margin: 8px 0 16px; }
th, td { text-align: left; padding: 4px 10px; border-bottom: 1px solid var(--border); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  .legend { border-left: 0; border-bottom: 1px solid var(--border); order: -1; }
  .legend ul { grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
  .io { grid-template-columns: 1fr; }
}
@media (max-width: 600px) {
  .mhead { grid-template-columns: 1fr; }
  header, main { padding-left: 16px; padding-right: 16px; }
}
@media (prefers-reduced-motion: no-preference) {
  .link, .node rect.frame, .cls { transition: opacity .15s, stroke .15s, border-color .15s; }
}
@media (forced-colors: active) {
  .link.against path { stroke-dasharray: 8 3; }
}
</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <nav class="crumbs" aria-label="Zoom level"><ol id="crumbs"></ol></nav>
  <fieldset class="toolbar" id="toolbar"><legend>Show</legend></fieldset>
</header>
<div class="layout">
  <main id="view" tabindex="-1"></main>
  <aside class="legend" aria-labelledby="legend-title"><h2 id="legend-title">Legend</h2><ul id="legend"></ul></aside>
</div>
<script type="application/json" id="zoom-data">__DATA__</script>
<script>
(function () {
"use strict";
var D = JSON.parse(document.getElementById("zoom-data").textContent);
var SVG = "http://www.w3.org/2000/svg";
var FILTERED = { tests: "Tests", vendor: "Framework / vendor", outside: "Outside " + (D.root ? D.root + "/" : "the code root") };
var state = { show: { tests: false, vendor: false, outside: false }, allLinks: false, allDomains: false };
var FOLD_AT = 15, TOP_LINKS = 15, COL_CAP = 30;
var LAYER_VIEW = D.layers.indexOf("View");

function el(tag, attrs, text) {
  var e = document.createElement(tag);
  for (var k in attrs || {}) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  return e;
}
function sv(tag, attrs, text) {
  var e = document.createElementNS(SVG, tag);
  for (var k in attrs || {}) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  return e;
}
function plural(n, one, many) { return n.toLocaleString("en") + " " + (n === 1 ? one : (many || one + "s")); }
function fileLine(f, line) { return D.files[f] + (line ? ":" + line : ""); }
function domainShown(i) { var k = D.domains[i].kind; return !(k in FILTERED) || state.show[k]; }
function classShown(c) { return domainShown(D.classes[c][1]); }
function domainLabel(i) { return D.domains[i].name; }
function methodName(m) { return D.methods[m][1].replace(/^\./, ""); }
function breakable(node, text) {
  text.split(/(?<=[a-z0-9])(?=[A-Z])|(?<=[._/-])/).forEach(function (part, i) {
    if (i) node.appendChild(document.createElement("wbr"));
    node.appendChild(document.createTextNode(part));
  });
  return node;
}
function againstIcon() {
  var s = sv("svg", { "class": "icon-against", viewBox: "0 0 16 16", "aria-hidden": "true" });
  s.appendChild(sv("path", { d: "M8 1 15.5 14.5H.5Z" }));
  s.appendChild(sv("text", { x: "8", y: "13", "text-anchor": "middle" }, "!"));
  return s;
}

/* ---------- toolbar ---------- */
function buildToolbar() {
  var bar = document.getElementById("toolbar");
  var counts = { tests: 0, vendor: 0, outside: 0 };
  D.domains.forEach(function (d) { if (d.kind in counts) counts[d.kind] += d.classes; });
  Object.keys(FILTERED).forEach(function (k) {
    if (!counts[k]) return;
    addCheck(bar, FILTERED[k] + " (" + plural(counts[k], "class", "classes") + ")", state.show[k], function (v) { state.show[k] = v; });
  });
  allDomainsBox = addCheck(bar, "All domains", state.allDomains, function (v) { state.allDomains = v; });
  addCheck(bar, "All links", state.allLinks, function (v) { state.allLinks = v; });
}
var allDomainsBox = null;
function addCheck(bar, text, checked, onChange) {
  var label = el("label"), box = el("input", { type: "checkbox" });
  box.checked = checked;
  box.addEventListener("change", function () { onChange(box.checked); render(); });
  label.appendChild(box); label.appendChild(document.createTextNode(text));
  bar.appendChild(label);
  return box;
}

/* ---------- routing ---------- */
function route() {
  var m = /^#\/(d|c)\/(\d+)$/.exec(location.hash);
  if (m && m[1] === "d" && D.domains[+m[2]] && domainShown(+m[2])) return { level: 2, domain: +m[2] };
  if (m && m[1] === "c" && D.classes[+m[2]] && classShown(+m[2])) return { level: 3, cls: +m[2], domain: D.classes[+m[2]][1] };
  return { level: 1 };
}
function crumbs(r) {
  var ol = document.getElementById("crumbs");
  ol.textContent = "";
  var items = [["#/", "Domains"]];
  if (r.level >= 2) items.push(["#/d/" + r.domain, domainLabel(r.domain)]);
  if (r.level === 3) items.push(["#/c/" + r.cls, D.classes[r.cls][0]]);
  items.forEach(function (it, i) {
    var li = el("li"), a = el("a", { href: it[0] }, it[1]);
    if (i === items.length - 1) a.setAttribute("aria-current", "page");
    li.appendChild(a); ol.appendChild(li);
  });
}
var lastRoute = "";
function render() {
  var r = route(), view = document.getElementById("view");
  crumbs(r);
  view.textContent = "";
  if (!D.classes.length) {
    view.appendChild(el("h2", { tabindex: "-1" }, "No classes in this graph"));
    view.appendChild(el("p", { "class": "lede" }, "This view needs class nodes from a code extraction. Run graphify on a code repository, then export again."));
    legend([]);
    return;
  }
  if (r.level === 1) level1(view); else if (r.level === 2) level2(view, r.domain); else level3(view, r.cls);
  var key = location.hash;
  if (key !== lastRoute) { lastRoute = key; var h = view.querySelector("h2"); if (h) h.focus(); }
}
window.addEventListener("hashchange", render);
document.addEventListener("keydown", function (e) {
  if (e.key !== "Escape" || /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
  var r = route();
  if (r.level === 3) location.hash = "#/d/" + r.domain; else if (r.level === 2) location.hash = "#/";
});

function legend(items) {
  var ul = document.getElementById("legend");
  ul.textContent = "";
  items.forEach(function (it) {
    var li = el("li"), s = sv("svg", { width: "44", height: "24", viewBox: "0 0 44 24", "aria-hidden": "true" });
    it[0](s);
    li.appendChild(s); li.appendChild(el("span", {}, it[1]));
    ul.appendChild(li);
  });
}
var SWATCH = {
  bar: function (s) { s.appendChild(sv("rect", { x: 2, y: 9, width: 40, height: 6, rx: 1, style: "fill:var(--panel)" })); s.appendChild(sv("rect", { x: 2, y: 9, width: 26, height: 6, rx: 1, style: "fill:var(--bar)" })); },
  link: function (s) { s.appendChild(sv("path", { d: "M2 12H36", style: "stroke:var(--edge);stroke-width:2.5" })); s.appendChild(sv("path", { d: "M34 7 42 12 34 17Z", style: "fill:var(--edge)" })); },
  inferred: function (s) { s.appendChild(sv("path", { d: "M2 12H40", style: "stroke:var(--edge);stroke-width:2;stroke-dasharray:2 4" })); },
  against: function (s) { s.appendChild(sv("path", { d: "M2 12H40", style: "stroke:var(--danger);stroke-width:3" })); s.appendChild(sv("circle", { cx: 22, cy: 12, r: 8, style: "fill:var(--danger)" })); s.appendChild(sv("text", { x: 22, y: 16, "text-anchor": "middle", style: "fill:var(--on-danger);font-weight:700;font-size:12px" }, "!")); },
  rows: function (s) { [3, 15].forEach(function (y) { s.appendChild(sv("rect", { x: 6, y: y, width: 32, height: 7, rx: 1, style: "fill:var(--box);stroke:var(--border)" })); }); s.appendChild(sv("path", { d: "M22 10V15", style: "stroke:var(--edge);stroke-width:1.5" })); },
  classAgainst: function (s) { s.appendChild(sv("rect", { x: 2, y: 3, width: 40, height: 18, rx: 2, style: "fill:var(--box);stroke:var(--danger);stroke-width:2" })); s.appendChild(sv("path", { d: "M10 6 16 17H4Z", style: "fill:var(--danger)" })); },
  columns: function (s) { [2, 16, 30].forEach(function (x) { s.appendChild(sv("rect", { x: x, y: 3, width: 12, height: 18, rx: 1, style: "fill:var(--panel);stroke:var(--border)" })); }); },
  badge: function (s) { s.appendChild(sv("rect", { x: 6, y: 2, width: 32, height: 20, rx: 10, style: "fill:var(--danger)" })); s.appendChild(sv("text", { x: 22, y: 16, "text-anchor": "middle", style: "fill:var(--on-danger);font-weight:700;font-size:12px" }, "! 2")); },
  fold: function (s) { s.appendChild(sv("rect", { x: 2, y: 4, width: 40, height: 16, rx: 2, style: "fill:var(--box);stroke:var(--border);stroke-dasharray:4 3" })); }
};

/* ---------- level 1: domains ---------- */
function level1Model() {
  var vis = D.domains.map(function (d, i) { return i; }).filter(domainShown);
  vis.sort(function (a, b) { return D.domains[b].classes - D.domains[a].classes || D.domains[a].name.localeCompare(D.domains[b].name); });
  var fold = !state.allDomains && vis.length > FOLD_AT;
  var shown = fold ? vis.slice(0, FOLD_AT - 1) : vis, rest = fold ? vis.slice(FOLD_AT - 1) : [];
  var boxOf = {}, nodes = shown.map(function (i) {
    var d = D.domains[i], n = { key: "d" + i, idx: i, name: d.name, kind: d.kind, classes: d.classes, order: d.order, row: d.row };
    boxOf[i] = n; return n;
  });
  if (rest.length) {
    var o = { key: "fold", idx: -1, name: "+" + plural(rest.length, "domain"), kind: "fold", classes: 0, order: 1e9, row: 1e9, members: rest };
    rest.forEach(function (i) { o.classes += D.domains[i].classes; boxOf[i] = o; });
    nodes.push(o);
  }
  var agg = {};
  D.domainLinks.forEach(function (l) {
    var a = boxOf[l[0]], b = boxOf[l[1]];
    if (!a || !b || a === b) return;
    var k = a.key + ">" + b.key, x = agg[k] || (agg[k] = { from: a, to: b, ext: 0, inf: 0, against: 0 });
    x.ext += l[2]; x.inf += l[3]; if (l[4]) x.against += l[2];
  });
  var links = Object.keys(agg).map(function (k) { return agg[k]; });
  links.sort(function (a, b) { return (b.ext + b.inf) - (a.ext + a.inf) || a.from.name.localeCompare(b.from.name); });
  var forward = 0;
  links.forEach(function (l) {
    l.total = l.ext + l.inf;
    l.shownByDefault = state.allLinks || (!l.against && l.ext > 0 && forward++ < TOP_LINKS);
  });
  var rows = nodes.map(function (n) { return n.row; }).filter(function (v, i, a) { return a.indexOf(v) === i; }).sort(function (a, b) { return a - b; });
  nodes.forEach(function (n) { n.layer = rows.indexOf(n.row); });
  nodes.sort(function (a, b) { return a.layer - b.layer || a.order - b.order; });
  return { nodes: nodes, links: links };
}

function level1(view) {
  var m = level1Model();
  var maxClasses = Math.max.apply(null, m.nodes.map(function (n) { return n.classes; }).concat([1]));
  var against = m.links.filter(function (l) { return l.against; }).length;
  view.appendChild(el("h2", { tabindex: "-1" }, "Domains"));
  var hiddenLinks = m.links.filter(function (l) { return !l.shownByDefault && !l.against; }).length;
  view.appendChild(el("p", { "class": "lede" }, plural(m.nodes.length, "domain") + " shown, callers above the code they call" +
    (hiddenLinks ? ", with the " + TOP_LINKS + " strongest links (" + plural(hiddenLinks, "weaker link") + " under All links). " : ". ") +
    (against ? (against === 1 ? "1 dependency runs back up and closes a cycle: the domain marked “!” starts it." : against + " dependencies run back up and close cycles: the domains marked “!” start them.") : "No dependency runs back up.")));
  var details = el("p", { "class": "details", id: "details" }, "Hover or focus a domain to see its links. Open it to see its classes by layer.");
  view.appendChild(details);
  var wrap = el("div", { "class": "canvas" });
  view.appendChild(wrap);
  var W = 184, H = 66, GX = 28, GY = 84, SUB = 22;
  function draw() {
    wrap.textContent = "";
    var width = Math.max(wrap.clientWidth - 4, W + 8), perLine = Math.max(1, Math.floor((width + GX) / (W + GX)));
    var y = 8, pos = {}, maxX = 0, layers = {};
    m.nodes.forEach(function (n) { (layers[n.layer] = layers[n.layer] || []).push(n); });
    Object.keys(layers).sort(function (a, b) { return a - b; }).forEach(function (li) {
      var row = layers[li];
      for (var s = 0; s < row.length; s += perLine) {
        var line = row.slice(s, s + perLine), lineW = line.length * W + (line.length - 1) * GX;
        var x0 = Math.max(4, (Math.min(width, perLine * (W + GX) - GX) - lineW) / 2);
        line.forEach(function (n, j) { pos[n.key] = { x: x0 + j * (W + GX), y: y }; maxX = Math.max(maxX, x0 + j * (W + GX) + W); });
        y += H + (s + perLine < row.length ? SUB : GY);
      }
    });
    var svg = sv("svg", { width: Math.max(maxX + 8, width), height: y - GY + H / 2 + 16, role: "group", "aria-label": "Domains and the calls between them" });
    var defs = sv("defs");
    [["arrow", "var(--edge)"], ["arrow-against", "var(--danger)"]].forEach(function (a) {
      var mk = sv("marker", { id: a[0], viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse", markerUnits: "userSpaceOnUse" });
      mk.appendChild(sv("path", { d: "M0 0 10 5 0 10Z" })); defs.appendChild(mk);
    });
    svg.appendChild(defs);
    var gl = sv("g", { "class": "links" }), gn = sv("g", { "class": "nodes" }), gp = sv("g", { "class": "pills", "aria-hidden": "true" }), fitNames = [];
    var maxT = Math.max.apply(null, m.links.map(function (l) { return l.total; }).concat([1]));
    m.links.forEach(function (l) {
      var a = pos[l.from.key], b = pos[l.to.key], down = b.y > a.y;
      var x1 = a.x + W / 2, x2 = b.x + W / 2, y1, y2, d;
      if (down) { y1 = a.y + H; y2 = b.y; var my = (y1 + y2) / 2; d = "M" + x1 + " " + y1 + "C" + x1 + " " + my + " " + x2 + " " + my + " " + x2 + " " + y2; }
      else if (b.y < a.y) { y1 = a.y; y2 = b.y + H; var ny = (y1 + y2) / 2; x1 += 14; x2 += 14; d = "M" + x1 + " " + y1 + "C" + x1 + " " + ny + " " + x2 + " " + ny + " " + x2 + " " + y2; }
      else { y1 = y2 = a.y; var top = a.y - 30 - Math.min(40, Math.abs(x2 - x1) / 8); d = "M" + x1 + " " + y1 + "C" + x1 + " " + top + " " + x2 + " " + top + " " + x2 + " " + y2; }
      var cls = "link" + (l.against ? " against" : "") + (l.ext ? "" : " inferred") + (l.shownByDefault ? "" : " is-hidden");
      var g = sv("g", { "class": cls, "data-from": l.from.key, "data-to": l.to.key });
      var w = (l.against ? 2.5 : 1.25) + 3.5 * Math.log(1 + l.total) / Math.log(1 + maxT);
      var p = sv("path", { d: d, "stroke-width": w.toFixed(2), "marker-end": "url(#" + (l.against ? "arrow-against" : "arrow") + ")" });
      g.appendChild(p); gl.appendChild(g);
      var pg = sv("g", { "class": cls, "data-from": l.from.key, "data-to": l.to.key });
      gp.appendChild(pg);
      l.pill = { g: pg, path: p };
    });
    svg.appendChild(gl);
    m.nodes.forEach(function (n) {
      var p = pos[n.key], g;
      var label = n.name + ", " + plural(n.classes, "class", "classes") + linkSummary(n, m.links);
      if (n.kind === "fold") {
        g = sv("g", { "class": "node fold", tabindex: "0", role: "button", "aria-label": label + ". Show all domains." });
        var toggle = function () { state.allDomains = true; allDomainsBox.checked = true; render(); };
        g.addEventListener("click", toggle);
        g.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
      } else {
        g = sv("a", { "class": "node", href: "#/d/" + n.idx, "aria-label": label + ". Open domain." });
      }
      g.setAttribute("data-key", n.key);
      g.setAttribute("transform", "translate(" + p.x + "," + p.y + ")");
      g.appendChild(sv("rect", { "class": "frame", width: W, height: H, rx: 4 }));
      var t = sv("text", { "class": "name", x: 12, y: 24 }, n.name);
      g.appendChild(t);
      fitNames.push([t, n.name]);
      g.appendChild(sv("rect", { "class": "track", x: 12, y: 33, width: W - 24, height: 6, rx: 1 }));
      g.appendChild(sv("rect", { "class": "fill", x: 12, y: 33, width: Math.max(2, (W - 24) * n.classes / maxClasses).toFixed(1), height: 6, rx: 1 }));
      var back = m.links.filter(function (l) { return l.from === n && l.against; }).length;
      if (back) {
        var bt = "! " + back, bw = 14 + bt.length * 7.5, badge = sv("g", { "class": "badge", "aria-hidden": "true" });
        badge.appendChild(sv("rect", { x: W - bw - 6, y: 6, width: bw, height: 20, rx: 10 }));
        badge.appendChild(sv("text", { x: W - bw / 2 - 6, y: 20, "text-anchor": "middle" }, bt));
        g.appendChild(badge);
        fitNames[fitNames.length - 1].push(W - bw - 30);
      }
      g.appendChild(sv("text", { "class": "sub", x: 12, y: 56 }, plural(n.classes, "class", "classes") + (n.kind === "none" ? " · no folder" : n.kind === "fold" ? " · show all" : "")));
      ["mouseenter", "focus"].forEach(function (ev) { g.addEventListener(ev, function () { activate(svg, n, m); }); });
      ["mouseleave", "blur"].forEach(function (ev) { g.addEventListener(ev, function () { activate(svg, null, m); }); });
      gn.appendChild(g);
    });
    svg.appendChild(gn);
    svg.appendChild(gp);
    wrap.appendChild(svg);
    fitNames.forEach(function (f) {
      var max = f[2] || W - 24, s = f[1];
      while (s.length > 1 && f[0].getComputedTextLength() > max) { s = s.slice(0, -1); f[0].textContent = s + "…"; }
    });
    var taken = m.nodes.map(function (n) { var p = pos[n.key]; return [p.x - 4, p.y - 4, p.x + W + 4, p.y + H + 4, 10]; });
    m.links.forEach(function (l) {
      var txt = l.total.toLocaleString("en"), tw = Math.max(20, txt.length * 7.5 + (l.against ? 14 : 8));
      var pt = freeSpot(l.pill.path, tw, taken);
      l.pill.g.appendChild(sv("rect", { "class": "pill", x: (pt.x - tw / 2).toFixed(1), y: (pt.y - 10).toFixed(1), width: tw.toFixed(1), height: 20, rx: 10 }));
      l.pill.g.appendChild(sv("text", { x: pt.x.toFixed(1), y: (pt.y + 4).toFixed(1), "text-anchor": "middle" }, (l.against ? "! " : "") + txt));
    });
  }
  draw();
  observeWidth(wrap, draw);
  view.appendChild(level1Table(m));
  legend([[SWATCH.rows, "Rows: callers above, the domains they call below."], [SWATCH.bar, "Bar length: number of classes."],
    [SWATCH.link, "Arrow: method calls between two domains, labelled with their number. Thicker = more calls."],
    [SWATCH.badge, "“!” badge: the domain calls back up into a domain above it, closing a dependency cycle. Hover or focus it to see the red links."],
    [SWATCH.against, "Red with “!”: a link running back up."],
    [SWATCH.inferred, "Dotted: only inferred calls (resolved by name, not proven)."],
    [SWATCH.fold, "Dashed box: the smaller domains, grouped."]]);
}
function freeSpot(path, tw, taken) {
  var len = path.getTotalLength(), best = null, bestScore = Infinity;
  [0.3, 0.4, 0.22, 0.5, 0.15, 0.6, 0.1, 0.7, 0.8, 0.9].forEach(function (f) {
    if (bestScore === 0) return;
    var pt = path.getPointAtLength(len * f), box = [pt.x - tw / 2 - 2, pt.y - 12, pt.x + tw / 2 + 2, pt.y + 12];
    var score = taken.reduce(function (acc, r) { return acc + (box[0] < r[2] && box[2] > r[0] && box[1] < r[3] && box[3] > r[1] ? (r[4] || 1) : 0); }, 0);
    if (score < bestScore) { best = pt; bestScore = score; }
  });
  taken.push([best.x - tw / 2 - 2, best.y - 12, best.x + tw / 2 + 2, best.y + 12]);
  return best;
}
function linkSummary(n, links) {
  var out = 0, inn = 0, ag = 0;
  links.forEach(function (l) { if (l.from === n) { out += l.total; ag += l.against; } if (l.to === n) inn += l.total; });
  return ", calls out " + out.toLocaleString("en") + ", calls in " + inn.toLocaleString("en") + (ag ? ", " + plural(ag, "call") + " against the main direction" : "");
}
function activate(svg, n, m) {
  var gl = svg.querySelectorAll(".links, .pills");
  svg.querySelectorAll(".node").forEach(function (g) { g.classList.remove("is-active", "is-partner"); });
  svg.querySelectorAll(".link").forEach(function (g) { g.classList.remove("is-related"); });
  var details = document.getElementById("details");
  if (!n) { gl.forEach(function (g) { g.classList.remove("has-active"); }); details.textContent = "Hover or focus a domain to see its links. Open it to see its classes by layer."; return; }
  gl.forEach(function (g) { g.classList.add("has-active"); });
  svg.querySelector('[data-key="' + n.key + '"]').classList.add("is-active");
  var out = [], inn = [];
  m.links.forEach(function (l) {
    if (l.from !== n && l.to !== n) return;
    var other = l.from === n ? l.to : l.from;
    svg.querySelectorAll('.link[data-from="' + l.from.key + '"][data-to="' + l.to.key + '"]').forEach(function (g) { g.classList.add("is-related"); });
    var node = svg.querySelector('[data-key="' + other.key + '"]'); if (node) node.classList.add("is-partner");
    (l.from === n ? out : inn).push(other.name + " " + l.total.toLocaleString("en") + (l.against ? " (against)" : ""));
  });
  details.textContent = "";
  details.appendChild(el("strong", {}, n.name));
  details.appendChild(document.createTextNode(": calls " + (out.join(", ") || "nothing") + ". Called by " + (inn.join(", ") || "nothing") + "."));
}
function level1Table(m) {
  var det = el("details", { "class": "table-view" });
  det.appendChild(el("summary", {}, "Domains and links as a table"));
  var w1 = el("div", { "class": "tablewrap" }), t1 = el("table");
  t1.appendChild(el("caption", { "class": "sr-only" }, "Domains"));
  var h = el("tr"); ["Domain", "Classes"].forEach(function (c, i) { h.appendChild(el("th", { scope: "col", "class": i ? "num" : "" }, c)); }); t1.appendChild(h);
  m.nodes.forEach(function (n) { var r = el("tr"); r.appendChild(el("td", {}, n.name)); r.appendChild(el("td", { "class": "num" }, n.classes.toLocaleString("en"))); t1.appendChild(r); });
  w1.appendChild(t1); det.appendChild(w1);
  var w2 = el("div", { "class": "tablewrap" }), t2 = el("table");
  t2.appendChild(el("caption", { "class": "sr-only" }, "Links between domains"));
  h = el("tr"); ["From", "To", "Proven calls", "Inferred calls", "Direction"].forEach(function (c, i) { h.appendChild(el("th", { scope: "col", "class": i > 1 && i < 4 ? "num" : "" }, c)); }); t2.appendChild(h);
  m.links.forEach(function (l) {
    var r = el("tr");
    r.appendChild(el("td", {}, l.from.name)); r.appendChild(el("td", {}, l.to.name));
    r.appendChild(el("td", { "class": "num" }, l.ext.toLocaleString("en"))); r.appendChild(el("td", { "class": "num" }, l.inf.toLocaleString("en")));
    r.appendChild(el("td", {}, l.against ? "against the main direction" : "with the main direction"));
    t2.appendChild(r);
  });
  w2.appendChild(t2); det.appendChild(w2);
  return det;
}
var observers = [];
function observeWidth(node, fn) {
  observers.forEach(function (o) { o.disconnect(); }); observers = [];
  if (!window.ResizeObserver) return;
  var last = node.clientWidth, o = new ResizeObserver(function () { if (Math.abs(node.clientWidth - last) > 8) { last = node.clientWidth; fn(); } });
  o.observe(node); observers.push(o);
}

/* ---------- level 2: one domain, classes by layer ---------- */
function level2(view, di) {
  var members = [], inDomain = {};
  D.classes.forEach(function (c, i) { if (c[1] === di) { members.push(i); inDomain[i] = true; } });
  var partners = {}, degree = {}, against = {}, flows = {}, outD = {}, inD = {};
  function link(a, b) { (partners[a] = partners[a] || {})[b] = true; degree[a] = (degree[a] || 0) + 1; }
  D.classLinks.forEach(function (l) {
    var s = l[0], t = l[1];
    if (inDomain[s] && inDomain[t]) {
      link(s, t); link(t, s);
      if (l[4]) against[s] = (against[s] || 0) + l[2];
      var ls = D.classes[s][2], lt = D.classes[t][2];
      if (ls !== lt) { var k = ls + ">" + lt, f = flows[k] || (flows[k] = { from: ls, to: lt, ext: 0, inf: 0, against: 0 }); f.ext += l[2]; f.inf += l[3]; if (l[4]) f.against += l[2]; }
    } else if (inDomain[s] && classShown(t)) { var dt = D.classes[t][1]; outD[dt] = (outD[dt] || 0) + l[2] + l[3]; }
    else if (inDomain[t] && classShown(s)) { var ds = D.classes[s][1]; inD[ds] = (inD[ds] || 0) + l[2] + l[3]; }
  });
  var tpl = {};
  D.renders.forEach(function (r) {
    if (!inDomain[r[0]]) return;
    tpl[r[1]] = true; link(r[0], "t" + r[1]); link("t" + r[1], r[0]);
    var k = D.classes[r[0]][2] + ">" + LAYER_VIEW, f = flows[k] || (flows[k] = { from: D.classes[r[0]][2], to: LAYER_VIEW, ext: 0, inf: 0, against: 0 });
    f.ext += r[2];
  });
  var nAgainst = Object.keys(against).length;
  view.appendChild(el("h2", { tabindex: "-1" }, domainLabel(di)));
  view.lastChild.appendChild(el("small", {}, plural(members.length, "class", "classes") + " by layer"));
  view.appendChild(el("p", { "class": "lede" }, "Each layer calls the layers to its right. " +
    (nAgainst ? plural(nAgainst, "class", "classes") + " call a layer to their left (outlined in red)." : "No class calls a layer to its left.")));
  view.appendChild(neighbours(outD, inD));
  view.appendChild(el("p", { "class": "details", id: "details" }, "Hover or focus a class to outline the classes it is linked to in this domain. Open it to see its methods."));
  var cols = D.layers.map(function () { return []; });
  members.forEach(function (i) { cols[D.classes[i][2]].push(i); });
  cols.forEach(function (list) { list.sort(function (a, b) { return (against[b] ? 1 : 0) - (against[a] ? 1 : 0) || (degree[b] || 0) - (degree[a] || 0) || D.classes[a][0].localeCompare(D.classes[b][0]); }); });
  var flowsWrap = el("div", { "class": "canvas" }), grid = el("div", { "class": "columns" });
  view.appendChild(flowsWrap); view.appendChild(grid);
  var heads = {};
  D.layers.forEach(function (name, li) {
    var isView = li === LAYER_VIEW, items = isView ? Object.keys(tpl).map(Number) : cols[li];
    if (!items.length) return;
    var sec = el("section", { "class": "col", "aria-labelledby": "col-" + li }), h3 = el("h3", { id: "col-" + li });
    h3.appendChild(document.createTextNode(name)); h3.appendChild(el("span", {}, items.length.toLocaleString("en")));
    sec.appendChild(h3); heads[li] = sec;
    var ul = el("ul"); sec.appendChild(ul);
    function add(from, to) {
      items.slice(from, to).forEach(function (i) {
        var li2 = el("li"), node;
        if (isView) {
          node = breakable(el("span", { "class": "cls tpl", "data-key": "t" + i }), D.templates[i][0]);
        } else {
          var c = D.classes[i];
          node = el("a", { "class": "cls" + (against[i] ? " against" : ""), href: "#/c/" + i, "data-key": String(i),
            "aria-label": c[0] + ", " + plural(c[5], "method") + ", linked to " + plural(Object.keys(partners[i] || {}).length, "class", "classes") + " here" + (against[i] ? ", calls a layer to its left " + plural(against[i], "time") : "") });
          if (against[i]) node.appendChild(againstIcon());
          breakable(node, c[0]);
          node.addEventListener("mouseenter", function () { highlight(grid, i, partners, members); });
          node.addEventListener("focus", function () { highlight(grid, i, partners, members); });
          node.addEventListener("mouseleave", function () { highlight(grid, null); });
          node.addEventListener("blur", function () { highlight(grid, null); });
        }
        li2.appendChild(node); ul.appendChild(li2);
      });
    }
    add(0, COL_CAP);
    if (items.length > COL_CAP) {
      var more = el("button", { type: "button", "class": "more" }, "Show " + plural(items.length - COL_CAP, "more class", "more classes"));
      more.addEventListener("click", function () { var first = ul.children.length; add(COL_CAP, items.length); more.remove(); var nx = ul.children[first]; if (nx) nx.firstChild.focus(); });
      sec.appendChild(more);
    }
    grid.appendChild(sec);
  });
  var flowList = Object.keys(flows).map(function (k) { return flows[k]; });
  function drawFlows() { drawFlowArcs(flowsWrap, grid, heads, flowList); }
  requestAnimationFrame(drawFlows);
  observeWidth(grid, drawFlows);
  view.appendChild(flowTable(flowList));
  legend([[SWATCH.columns, "Columns: layers, left to right. A layer calls the layers to its right."],
    [SWATCH.link, "Arrow above the columns: calls from one layer to another, with their number."],
    [SWATCH.against, "Red with “!”: calls to a layer on the left, the forbidden direction."],
    [SWATCH.classAgainst, "Red outline and triangle: a class that makes such a call."],
    [SWATCH.inferred, "Dotted: only inferred calls (resolved by name, not proven)."]]);
}
function neighbours(outD, inD) {
  var box = el("div", { "class": "neighbours" });
  [[outD, "Calls into"], [inD, "Called from"]].forEach(function (p) {
    var keys = Object.keys(p[0]).map(Number).sort(function (a, b) { return p[0][b] - p[0][a]; });
    if (!keys.length) return;
    var line = el("p", { style: "margin:0" }); line.appendChild(document.createTextNode(p[1] + " other domains: "));
    keys.slice(0, 6).forEach(function (d, i) {
      if (i) line.appendChild(document.createTextNode(", "));
      line.appendChild(el("a", { href: "#/d/" + d }, D.domains[d].name)); line.appendChild(document.createTextNode(" " + p[0][d].toLocaleString("en")));
    });
    if (keys.length > 6) line.appendChild(document.createTextNode(" and " + (keys.length - 6) + " more"));
    box.appendChild(line);
  });
  return box;
}
function highlight(grid, i, partners, members) {
  grid.querySelectorAll(".cls").forEach(function (n) { n.classList.remove("is-active", "is-partner"); });
  var details = document.getElementById("details");
  if (i == null) { details.textContent = "Hover or focus a class to outline the classes it is linked to in this domain. Open it to see its methods."; return; }
  var mine = partners[i] || {};
  grid.querySelectorAll(".cls").forEach(function (n) { var k = n.getAttribute("data-key"); if (k === String(i)) n.classList.add("is-active"); else if (mine[k]) n.classList.add("is-partner"); });
  var c = D.classes[i];
  details.textContent = "";
  details.appendChild(el("strong", {}, c[0]));
  details.appendChild(document.createTextNode(": " + D.layers[c[2]] + ", " + plural(c[5], "method") + ", linked to " + plural(Object.keys(mine).length, "class", "classes") + " or templates in this domain. " + fileLine(c[3], c[4])));
}
function drawFlowArcs(wrap, grid, heads, flows) {
  wrap.textContent = "";
  var keys = Object.keys(heads);
  if (keys.length < 2 || !flows.length) return;
  var gr = grid.getBoundingClientRect(), top = null, x = {};
  for (var i = 0; i < keys.length; i++) {
    var r = heads[keys[i]].getBoundingClientRect();
    if (top === null) top = r.top; else if (Math.abs(r.top - top) > 2) return;
    x[keys[i]] = r.left - gr.left + r.width / 2;
  }
  var order = keys.map(Number).sort(function (a, b) { return x[a] - x[b]; });
  var maxT = Math.max.apply(null, flows.map(function (f) { return f.ext + f.inf; }).concat([1]));
  var maxSpan = 1;
  flows.forEach(function (f) { if (x[f.from] != null && x[f.to] != null) maxSpan = Math.max(maxSpan, Math.abs(order.indexOf(f.from) - order.indexOf(f.to))); });
  var H = 34 + 26 * maxSpan, svg = sv("svg", { width: gr.width, height: H, "class": "flows", "aria-hidden": "true" });
  var defs = sv("defs");
  [["farrow", "--edge"], ["farrow-against", "--danger"]].forEach(function (a) {
    var mk = sv("marker", { id: a[0], viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto", markerUnits: "userSpaceOnUse" });
    mk.appendChild(sv("path", { d: "M0 0 10 5 0 10Z", style: "fill:var(" + a[1] + ")" })); defs.appendChild(mk);
  });
  svg.appendChild(defs);
  var pills = sv("g"), labels = [];
  flows.forEach(function (f) {
    if (x[f.from] == null || x[f.to] == null) return;
    var span = Math.abs(order.indexOf(f.from) - order.indexOf(f.to)), ltr = x[f.to] > x[f.from];
    var x1 = x[f.from] + (ltr ? 6 : -6), x2 = x[f.to] + (ltr ? -6 : 6), peak = H - 8 - 26 * span + (ltr ? 0 : 14);
    var bad = f.against > 0, total = f.ext + f.inf;
    var g = sv("g", { "class": "link" + (bad ? " against" : "") + (f.ext ? "" : " inferred") });
    var path = g.appendChild(sv("path", { d: "M" + x1 + " " + (H - 2) + "C" + x1 + " " + peak + " " + x2 + " " + peak + " " + x2 + " " + (H - 2),
      "stroke-width": ((bad ? 2.5 : 1.25) + 3 * Math.log(1 + total) / Math.log(1 + maxT)).toFixed(2), "marker-end": "url(#" + (bad ? "farrow-against" : "farrow") + ")" }));
    svg.appendChild(g);
    labels.push({ path: path, txt: bad ? "! " + f.against.toLocaleString("en") : total.toLocaleString("en"), bad: bad });
  });
  svg.appendChild(pills);
  wrap.appendChild(svg);
  var taken = [];
  labels.sort(function (a, b) { return b.bad - a.bad; }).forEach(function (l) {
    var tw = l.txt.length * 7.5 + 10, pt = freeSpot(l.path, tw, taken);
    var pg = sv("g", { "class": "link" + (l.bad ? " against" : "") });
    pg.appendChild(sv("rect", { "class": "pill", x: pt.x - tw / 2, y: pt.y - 10, width: tw, height: 20, rx: 10 }));
    pg.appendChild(sv("text", { x: pt.x, y: pt.y + 4, "text-anchor": "middle" }, l.txt));
    pills.appendChild(pg);
  });
}
function flowTable(flows) {
  var det = el("details", { "class": "table-view" });
  det.appendChild(el("summary", {}, "Calls between layers as a table"));
  var w = el("div", { "class": "tablewrap" }), t = el("table"), h = el("tr");
  ["From layer", "To layer", "Proven calls", "Inferred calls", "Forbidden direction"].forEach(function (c, i) { h.appendChild(el("th", { scope: "col", "class": i > 1 ? "num" : "" }, c)); });
  t.appendChild(h);
  flows.slice().sort(function (a, b) { return b.against - a.against || (b.ext + b.inf) - (a.ext + a.inf); }).forEach(function (f) {
    var r = el("tr");
    [D.layers[f.from], D.layers[f.to]].forEach(function (v) { r.appendChild(el("td", {}, v)); });
    [f.ext, f.inf, f.against].forEach(function (v) { r.appendChild(el("td", { "class": "num" }, v.toLocaleString("en"))); });
    t.appendChild(r);
  });
  w.appendChild(t); det.appendChild(w);
  return det;
}

/* ---------- level 3: one class ---------- */
function level3(view, ci) {
  var c = D.classes[ci], against = {};
  D.classLinks.forEach(function (l) { if (l[4] && l[0] === ci) against[l[1]] = true; });
  var mine = [], byMethod = {};
  D.methods.forEach(function (m, i) { if (m[0] === ci) { mine.push(i); byMethod[i] = { callers: [], callees: [] }; } });
  var self = { callers: [], callees: [] }, hidden = 0, callerClasses = {}, calleeClasses = {};
  D.calls.forEach(function (k) {
    if (k[2] === ci && k[0] !== ci) { if (!classShown(k[0])) { hidden++; return; } callerClasses[k[0]] = true; (k[3] >= 0 ? byMethod[k[3]] : self).callers.push(k); }
    if (k[0] === ci) { if (k[2] !== ci && !classShown(k[2])) { hidden++; return; } if (k[2] !== ci) calleeClasses[k[2]] = true; (k[1] >= 0 && byMethod[k[1]] ? byMethod[k[1]] : self).callees.push(k); }
  });
  var h2 = el("h2", { tabindex: "-1" }, c[0]);
  h2.appendChild(el("small", {}, D.layers[c[2]] + " · " + domainLabel(c[1])));
  view.appendChild(h2);
  view.appendChild(el("p", { "class": "loc" }, fileLine(c[3], c[4])));
  view.appendChild(el("p", { "class": "lede" }, plural(mine.length, "method") + ", called from " + plural(Object.keys(callerClasses).length, "other class", "other classes") +
    ", calls " + plural(Object.keys(calleeClasses).length, "other class", "other classes") + "." + (hidden ? " " + plural(hidden, "call") + " to or from hidden groups (see Show)." : "")));
  mine.sort(function (a, b) { return byMethod[b].callers.length - byMethod[a].callers.length || D.methods[a][1].localeCompare(D.methods[b][1]); });
  var max = 1;
  mine.forEach(function (i) { max = Math.max(max, byMethod[i].callers.length); });
  var ol = el("ol", { "class": "methods" });
  if (self.callers.length || self.callees.length) ol.appendChild(methodRow("(class itself)", c[3], c[4], self, max, against, ci));
  mine.forEach(function (i) { ol.appendChild(methodRow(methodName(i), c[3], D.methods[i][2], byMethod[i], max, against, ci)); });
  if (!ol.children.length) view.appendChild(el("p", { "class": "empty" }, "No method and no call recorded for this class."));
  view.appendChild(ol);
  legend([[SWATCH.bar, "Bar length: number of calls to the method from other classes."],
    [SWATCH.against, "“forbidden direction” tag: a call to a layer on the left."],
    [SWATCH.inferred, "“inferred” tag: resolved by name, not proven."]]);
}
function methodRow(name, file, line, io, max, against, ci) {
  var li = el("li", { "class": "method" }), head = el("div", { "class": "mhead" });
  head.appendChild(el("code", {}, name));
  var bar = el("span", { "class": "mbar", role: "img", "aria-label": plural(io.callers.length, "caller") }), fill = el("i");
  fill.style.width = (100 * io.callers.length / max).toFixed(1) + "%";
  bar.appendChild(fill); head.appendChild(bar);
  head.appendChild(el("span", { "class": "loc" }, fileLine(file, line)));
  li.appendChild(head);
  var box = el("div", { "class": "io" });
  box.appendChild(callList("Called by", io.callers, true, against, ci));
  box.appendChild(callList("Calls", io.callees, false, against, ci));
  li.appendChild(box);
  return li;
}
function callList(title, list, incoming, against, ci) {
  var div = el("div");
  div.appendChild(el("h4", {}, title + " (" + list.length.toLocaleString("en") + ")"));
  if (!list.length) { div.appendChild(el("p", { "class": "empty", style: "margin:0;font-size:.88rem" }, "none")); return div; }
  var ul = el("ul"); div.appendChild(ul);
  function add(from, to) {
    list.slice(from, to).forEach(function (k) {
      var cls = incoming ? k[0] : k[2], m = incoming ? k[1] : k[3];
      var item = el("li"), label = D.classes[cls][0] + (m >= 0 ? "." + methodName(m) : "");
      if (cls === ci) item.appendChild(el("span", {}, label)); else item.appendChild(el("a", { href: "#/c/" + cls }, label));
      item.appendChild(el("span", { "class": "loc" }, fileLine(k[4], k[5])));
      if (!k[6]) item.appendChild(el("span", { "class": "tag" }, "inferred"));
      if (!incoming && k[6] && against[k[2]]) item.appendChild(el("span", { "class": "tag against" }, "! forbidden direction"));
      ul.appendChild(item);
    });
  }
  add(0, 12);
  if (list.length > 12) {
    var more = el("button", { type: "button", "class": "more" }, "Show " + plural(list.length - 12, "more call"));
    more.addEventListener("click", function () { var first = ul.children.length; add(12, list.length); more.remove(); var nx = ul.children[first]; if (nx) nx.firstChild.focus(); });
    div.appendChild(more);
  }
  return div;
}

document.getElementById("title").appendChild(document.createTextNode(D.project || "graphify"));
document.getElementById("title").appendChild(el("span", {}, " · architecture zoom"));
buildToolbar();
render();
})();
</script>
</body>
</html>
"""
