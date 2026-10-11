"""Laravel Blade template extractor."""
from __future__ import annotations

from pathlib import Path

from graphify.extractors.base import _corpus_relative_path, _make_id, _read_source_text

# Laravel's default view path and FileViewFinder's extension order.
_VIEWS_DIR = Path("resources") / "views"
_VIEW_EXTENSIONS = ("blade.php", "php")
_MAX_WALK_UP = 12


def _views_root(referrer: Path) -> Path | None:
    """Nearest `resources/views` dir above *referrer*: the app's default view path."""
    current = referrer.parent
    for _ in range(_MAX_WALK_UP):
        views = current / _VIEWS_DIR
        try:
            if views.is_dir():
                return views
        except OSError:
            return None
        if current.parent == current:
            return None
        current = current.parent
    return None


def resolve_blade_name(name: str, referrer: Path) -> Path | None:
    """File a view name maps to under `resources/views`, or None (namespaced, dynamic or missing)."""
    if "::" in name or any(c in name for c in "$\\{}"):
        return None
    parts = name.split(".")
    if any(not p or p.startswith("/") or ".." in Path(p).parts for p in parts):
        return None
    root = _views_root(referrer)
    if root is None:
        return None
    stem = "/".join(parts)
    for ext in _VIEW_EXTENSIONS:
        candidate = root / f"{stem}.{ext}"
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            return None
    return None


def extract_blade(path: Path) -> dict:
    """Extract @extends, @include, <livewire:> components, and wire:click bindings from Blade templates."""
    import re
    try:
        src = _read_source_text(path)
    except OSError:
        return {"error": f"cannot read {path}"}

    file_nid = _make_id(str(path))
    nodes = [{"id": file_nid, "label": path.name, "file_type": "code",
              "source_file": str(path), "source_location": None}]
    edges = []

    seen_view_edges: set[tuple[str, str]] = set()

    def add_view_ref(name: str, relation: str) -> None:
        # Only a view found under resources/views links to a file node; anything else keeps its stub.
        resolved = resolve_blade_name(name, path)
        target_file = _corpus_relative_path(resolved, path) if resolved is not None else None
        tgt_nid = _make_id(target_file if target_file is not None else name.replace(".", "/"))
        if (tgt_nid, relation) in seen_view_edges:
            return
        seen_view_edges.add((tgt_nid, relation))
        if target_file is None and tgt_nid not in {n["id"] for n in nodes}:
            nodes.append({"id": tgt_nid, "label": name, "file_type": "code",
                          "source_file": str(path), "source_location": None})
        edge = {"source": file_nid, "target": tgt_nid, "relation": relation,
                "confidence": "EXTRACTED", "confidence_score": 1.0,
                "source_file": str(path), "source_location": None, "weight": 1.0}
        if target_file is not None:
            edge["target_file"] = target_file
        edges.append(edge)

    # @extends('layouts.app') is the view's layout, @include('partials.nav') a partial it embeds.
    for m in re.finditer(r"@extends\(['\"]([^'\"]+)['\"]", src):
        add_view_ref(m.group(1), "extends")
    for m in re.finditer(r"@include\(['\"]([^'\"]+)['\"]", src):
        add_view_ref(m.group(1), "includes")

    # <livewire:component.name /> or <livewire:component.name>
    for m in re.finditer(r"<livewire:([\w.\-]+)", src):
        tgt_nid = _make_id(m.group(1))
        if tgt_nid not in {n["id"] for n in nodes}:
            nodes.append({"id": tgt_nid, "label": m.group(1), "file_type": "code",
                          "source_file": str(path), "source_location": None})
        edges.append({"source": file_nid, "target": tgt_nid, "relation": "uses_component",
                      "confidence": "EXTRACTED", "confidence_score": 1.0,
                      "source_file": str(path), "source_location": None, "weight": 1.0})

    # wire:click="methodName"
    for m in re.finditer(r'wire:click=["\']([^"\']+)["\']', src):
        tgt_nid = _make_id(m.group(1))
        if tgt_nid not in {n["id"] for n in nodes}:
            nodes.append({"id": tgt_nid, "label": m.group(1), "file_type": "code",
                          "source_file": str(path), "source_location": None})
        edges.append({"source": file_nid, "target": tgt_nid, "relation": "binds_method",
                      "confidence": "EXTRACTED", "confidence_score": 1.0,
                      "source_file": str(path), "source_location": None, "weight": 1.0})

    return {"nodes": nodes, "edges": edges}
