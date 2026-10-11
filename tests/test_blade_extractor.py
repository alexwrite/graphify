"""Extraction coverage for Laravel Blade templates."""
from __future__ import annotations

from graphify.extract import extract


def _edge_labels(result: dict, relation: str) -> set[tuple[str, str]]:
    labels = {node["id"]: node["label"] for node in result["nodes"]}
    return {
        (labels.get(edge["source"], edge["source"]), labels.get(edge["target"], edge["target"]))
        for edge in result["edges"]
        if edge["relation"] == relation
    }


def test_blade_extends_links_view_to_its_layout(tmp_path):
    # @extends is template inheritance — the primary structural relationship in a
    # Blade view. Before the fix only @include was recognised, so a page that
    # extends a layout produced no edge to that layout at all.
    page = tmp_path / "page.blade.php"
    page.write_text(
        "@extends('layouts.app')\n"
        "@section('content')\n"
        "    @include('partials.nav')\n"
        "@endsection\n",
        encoding="utf-8",
    )

    result = extract([page], cache_root=tmp_path)

    assert ("page.blade.php", "layouts.app") in _edge_labels(result, "extends")
    # existing @include behaviour is preserved
    assert ("page.blade.php", "partials.nav") in _edge_labels(result, "includes")


def _laravel_views() -> dict:
    import tempfile
    from pathlib import Path

    root = (Path(__file__).parent / "fixtures" / "laravel_views").resolve()
    paths = sorted(p for p in root.rglob("*") if p.is_file())
    with tempfile.TemporaryDirectory() as td:
        return extract(paths, cache_root=Path(td), root=root)


def _to_files(result: dict, relation: str) -> set[tuple[str, str]]:
    """(source label, target source_file) for edges whose target is a file's own node."""
    from pathlib import Path

    from graphify.ids import make_id

    by_id = {node["id"]: node for node in result["nodes"]}
    return {
        (by_id[edge["source"]]["label"], by_id[edge["target"]]["source_file"])
        for edge in result["edges"]
        if edge["relation"] == relation
        and edge["target"] == make_id(str(Path(by_id[edge["target"]]["source_file"]).with_suffix("")))
    }


def test_blade_extends_and_include_target_the_view_file_node():
    result = _laravel_views()
    show = "resources/views/invoices/show.blade.php"
    assert _to_files(result, "extends") == {("show.blade.php", "resources/views/layouts/app.blade.php")}
    assert _to_files(result, "includes") == {("show.blade.php", "resources/views/partials/nav.blade.php")}
    # A namespaced or missing view stays a stub; nothing points at a phantom `layouts/app` node.
    stubs = {node["label"] for node in result["nodes"] if node.get("source_file") == show and node["label"] != "show.blade.php"}
    assert stubs == {"mail::message", "partials.missing"}
    duplicate_includes = [e for e in result["edges"] if e["relation"] == "includes" and e["target"].endswith("nav_blade")]
    assert len(duplicate_includes) == 1


def test_controller_view_calls_link_to_the_view_file():
    result = _laravel_views()
    by_id = {node["id"]: node for node in result["nodes"]}
    renders = {
        (by_id[e["source"]]["label"], by_id[e["target"]]["source_file"])
        for e in result["edges"]
        if e["relation"] == "renders"
    }
    # Concatenated, interpolated, namespaced and missing names yield no edge.
    assert renders == {
        (".show()", "resources/views/invoices/show.blade.php"),
        (".edit()", "resources/views/invoices/show.blade.php"),
        (".nav()", "resources/views/partials/nav.blade.php"),
    }
