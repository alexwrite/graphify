"""PHP code that renders a template: the call or attribute, resolved to the template file.

Only a literal template name is followed, and only when it resolves to a file on
disk; a name built at runtime (variable, concatenation, interpolation) yields no
edge.
"""
from __future__ import annotations

from pathlib import Path

from graphify.extractors.base import _corpus_relative_path, _make_id, _read_text
from graphify.extractors.twig import _resolve_path_ref as _resolve_twig_name

# Symfony AbstractController and Twig\Environment render methods (template first).
_TWIG_RENDER_METHODS = frozenset({"render", "renderView", "renderBlock", "renderBlockView"})

_RESOLVERS = {
    "twig": _resolve_twig_name,
}


def _string_literal(node, source: bytes) -> str | None:
    """Text of a PHP string with no interpolation, else None."""
    if node is None or node.type not in ("string", "encapsed_string"):
        return None
    parts = [c for c in node.children if c.is_named]
    if len(parts) != 1 or parts[0].type != "string_content":
        return None
    return _read_text(parts[0], source)


def _first_argument_literal(args_node, source: bytes, named: str) -> str | None:
    """Literal value of the first positional argument, or of the argument named *named*."""
    if args_node is None:
        return None
    for arg in args_node.children:
        if arg.type != "argument":
            continue
        name_node = arg.child_by_field_name("name")
        if name_node is not None and _read_text(name_node, source) != named:
            continue
        values = [c for c in arg.children if c.is_named and c != name_node]
        return _string_literal(values[0], source) if len(values) == 1 else None
    return None


def _short_name(node, source: bytes) -> str:
    return _read_text(node, source).rsplit("\\", 1)[-1]


def render_call_template(node, source: bytes) -> tuple[str, str] | None:
    """(engine, name) for a call that renders a literal template name, else None."""
    if node.type in ("member_call_expression", "nullsafe_member_call_expression"):
        method = node.child_by_field_name("name")
        if method is None or _read_text(method, source) not in _TWIG_RENDER_METHODS:
            return None
        name = _first_argument_literal(node.child_by_field_name("arguments"), source, "view")
        if name and name.endswith(".twig"):
            return ("twig", name)
    return None


def template_attribute_templates(method_node, source: bytes) -> list[tuple[str, str, int]]:
    """(engine, name, line) for each `#[Template('...')]` on a method."""
    found: list[tuple[str, str, int]] = []
    attributes = method_node.child_by_field_name("attributes")
    if attributes is None:
        return found
    for group in attributes.children:
        for attr in group.children:
            if attr.type != "attribute":
                continue
            name_node = next((c for c in attr.children if c.type in ("name", "qualified_name")), None)
            if name_node is None or _short_name(name_node, source) != "Template":
                continue
            name = _first_argument_literal(attr.child_by_field_name("parameters"), source, "template")
            if name and name.endswith(".twig"):
                found.append(("twig", name, attr.start_point[0] + 1))
    return found


def render_edge(ref: tuple[str, str], source_nid: str, php_path: Path, line: int) -> dict | None:
    """`renders` edge from *source_nid* to the template file *ref* names, if it exists."""
    engine, name = ref
    resolved = _RESOLVERS[engine](name, php_path)
    if resolved is None:
        return None
    target_file = _corpus_relative_path(resolved, php_path)
    return {
        "source": source_nid,
        "target": _make_id(target_file),
        "relation": "renders",
        "confidence": "EXTRACTED",
        "confidence_score": 1.0,
        "source_file": str(php_path),
        "source_location": f"L{line}",
        "weight": 1.0,
        "target_file": target_file,
    }
