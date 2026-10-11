"""Tests for graphify export zoom-html (graphify/zoom_html.py)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from graphify.zoom_html import (
    LAYERS,
    build_zoom_data,
    classify_domain,
    classify_layer,
    write_zoom_html,
)

PYTHON = sys.executable


@pytest.mark.parametrize("path,root,layer,domain", [
    ("src/Item/Controller/ItemController.php", "src", "controller", "Item"),
    ("src/Item/Entity/Item.php", "src", "entity", "Item"),
    ("src/Item/Pricing/PriceRule.php", "src", "other", "Item"),
    ("src/Controller/Item/ItemListController.php", "src", "controller", "Item"),
    ("src/Repository/ItemRepository.php", "src", "repository", None),
    ("src/Kernel.php", "src", "other", None),
    ("app/Http/Controllers/Item/ItemController.php", "app", "controller", "Item"),
    ("app/Http/Middleware/Locale.php", "app", "other", "Http"),
    ("app/Models/Item.php", "app", "entity", None),
    ("app/Services/Item/ItemService.php", "app", "service", "Item"),
    ("lib/Item/Service/ItemService.php", "src", "service", None),
])
def test_layer_and_domain_come_from_folder_names_only(path, root, layer, domain):
    assert classify_layer(path, root) == layer
    assert classify_domain(path, root) == domain


def _cls(cid, label, path):
    return {"id": cid, "label": label, "source_file": path, "source_location": "L3", "_callable_class": True}


def _meth(mid, label, path, line):
    return {"id": mid, "label": label, "source_file": path, "source_location": f"L{line}"}


def _graph() -> dict:
    nodes = [
        _cls("ctl", "ItemController", "src/Item/Controller/ItemController.php"),
        _meth("ctl_show", ".show()", "src/Item/Controller/ItemController.php", 10),
        _cls("svc", "ItemService", "src/Item/Service/ItemService.php"),
        _meth("svc_find", ".find()", "src/Item/Service/ItemService.php", 12),
        _meth("svc_price", ".price()", "src/Item/Service/ItemService.php", 20),
        _cls("repo", "ItemRepository", "src/Item/Repository/ItemRepository.php"),
        _meth("repo_load", ".load()", "src/Item/Repository/ItemRepository.php", 8),
        _cls("ent", "Item", "src/Item/Entity/Item.php"),
        _meth("ent_total", ".total()", "src/Item/Entity/Item.php", 30),
        _cls("clock", "SystemClock", "src/Shared/Clock/SystemClock.php"),
        _meth("clock_now", ".now()", "src/Shared/Clock/SystemClock.php", 5),
        _cls("test", "ItemTest", "tests/Item/ItemTest.php"),
        _meth("test_it", ".testIt()", "tests/Item/ItemTest.php", 9),
        _cls("lib", "Client", "vendor/acme/http/Client.php"),
        _cls("mig", "Version1", "migrations/Version1.php"),
        {"id": "tpl", "label": "show.html.twig", "source_file": "templates/item/show.html.twig"},
    ]
    def call(s, t, line, conf="EXTRACTED"):
        return {"source": s, "target": t, "relation": "calls", "confidence": conf,
                "source_file": "x.php", "source_location": f"L{line}"}
    links = [
        {"source": c, "target": m, "relation": "method"} for c, m in [
            ("ctl", "ctl_show"), ("svc", "svc_find"), ("svc", "svc_price"), ("repo", "repo_load"),
            ("ent", "ent_total"), ("clock", "clock_now"), ("test", "test_it")]
    ] + [
        call("ctl_show", "svc_find", 11),
        call("svc_find", "repo_load", 13),
        call("ent_total", "svc_price", 31),
        call("repo_load", "ctl_show", 9, conf="INFERRED"),
        call("svc_find", "clock_now", 14),
        call("svc_price", "clock_now", 21),
        call("clock_now", "ent_total", 6),
        call("test_it", "ctl_show", 10),
        call("ctl_show", "lib", 12),
        {"source": "ctl_show", "target": "tpl", "relation": "renders"},
        {"source": "mig", "target": "ent", "relation": "references"},
    ]
    return {"directed": False, "nodes": nodes, "links": links}


def _by_label(data):
    return {c[0]: i for i, c in enumerate(data["classes"])}


def _layer(data, label):
    return LAYERS[data["classes"][_by_label(data)[label]][2]][0]


def _domain(data, label):
    return data["domains"][data["classes"][_by_label(data)[label]][1]]


def test_groups_split_domains_from_tests_vendor_and_outside_code():
    data = build_zoom_data(_graph(), project="project-a")
    assert data["root"] == "src"
    assert _domain(data, "ItemController")["name"] == "Item"
    assert _domain(data, "SystemClock")["name"] == "Shared"
    assert _domain(data, "ItemTest")["kind"] == "tests"
    assert _domain(data, "Client")["kind"] == "vendor"
    assert _domain(data, "Version1")["kind"] == "outside"
    assert {d["name"]: d["classes"] for d in data["domains"] if d["kind"] == "domain"} == {"Item": 4, "Shared": 1}


def test_layers_follow_folders_and_unknown_folder_is_other():
    data = build_zoom_data(_graph())
    assert [_layer(data, n) for n in ("ItemController", "ItemService", "ItemRepository", "Item", "SystemClock")] == [
        "controller", "service", "repository", "entity", "other"]


def test_only_an_extracted_call_to_an_earlier_layer_is_marked_against():
    data = build_zoom_data(_graph())
    ix = _by_label(data)
    flags = {(data["classes"][s][0], data["classes"][t][0]): (ext, inf, ag) for s, t, ext, inf, ag in data["classLinks"]}
    assert flags[("Item", "ItemService")] == (1, 0, 1)
    assert flags[("ItemController", "ItemService")] == (1, 0, 0)
    assert flags[("ItemRepository", "ItemController")] == (0, 1, 0)
    assert flags[("ItemService", "SystemClock")] == (2, 0, 0)
    assert ix["ItemTest"] in {s for s, *_ in data["classLinks"]}


def test_domain_links_are_aggregated_and_the_minority_direction_closes_the_cycle():
    data = build_zoom_data(_graph())
    names = [d["name"] for d in data["domains"]]
    links = {(names[s], names[t]): (ext, inf, ag) for s, t, ext, inf, ag in data["domainLinks"]}
    assert links[("Item", "Shared")] == (2, 0, 0)
    assert links[("Shared", "Item")] == (1, 0, 1)
    item, shared = data["domains"][names.index("Item")], data["domains"][names.index("Shared")]
    assert item["order"] < shared["order"] and item["row"] < shared["row"]


def test_arc_direction_comes_from_src_tgt_markers_not_list_order():
    graph = _graph()
    for link in graph["links"]:
        if link.get("relation") == "calls" and link["source"] == "ent_total":
            link["_src"], link["_tgt"] = "ent_total", "svc_price"
            link["source"], link["target"] = "svc_price", "ent_total"
    data = build_zoom_data(graph)
    pairs = {(data["classes"][s][0], data["classes"][t][0]): ag for s, t, _, _, ag in data["classLinks"]}
    assert pairs[("Item", "ItemService")] == 1
    assert ("ItemService", "Item") not in pairs


def test_templates_join_a_domain_only_through_renders():
    data = build_zoom_data(_graph())
    assert [t[0] for t in data["templates"]] == ["show.html.twig"]
    assert data["renders"] == [[_by_label(data)["ItemController"], 0, 1]]


def test_level_three_keeps_methods_and_call_sites():
    data = build_zoom_data(_graph())
    ix = _by_label(data)
    assert data["classes"][ix["ItemService"]][5] == 2
    methods = {m[1]: m for m in data["methods"]}
    assert methods[".find()"][2] == 12
    call = next(c for c in data["calls"] if data["methods"][c[1]][1] == ".show()" and c[3] >= 0 and data["methods"][c[3]][1] == ".find()")
    assert data["files"][call[4]] == "x.php" and call[5] == 11 and call[6] == 1
    to_class = [c for c in data["calls"] if c[2] == ix["Client"]]
    assert to_class and to_class[0][3] == -1


def test_edges_key_graph_is_accepted():
    graph = _graph()
    graph["edges"] = graph.pop("links")
    assert len(build_zoom_data(graph)["classLinks"]) == len(build_zoom_data(_graph())["classLinks"])


def test_html_is_offline_and_labels_cannot_break_out_of_the_data_block(tmp_path):
    graph = _graph()
    graph["nodes"][0]["label"] = "</script><img src=x onerror=alert(1)>"
    out_dir = tmp_path / "graphify-out"
    out_dir.mkdir()
    (out_dir / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    out = write_zoom_html(out_dir / "graph.json", tmp_path / "zoom.html")
    html = out.read_text(encoding="utf-8")
    assert "<img src=x" not in html
    assert html.count("</script>") == 2
    assert "<script src" not in html and "https://" not in html
    assert "<title>" + tmp_path.name + " - architecture zoom</title>" in html
    assert "prefers-color-scheme: dark" in html and "prefers-reduced-motion" in html


def test_cli_export_zoom_html(tmp_path):
    out_dir = tmp_path / "graphify-out"
    out_dir.mkdir()
    (out_dir / "graph.json").write_text(json.dumps(_graph()), encoding="utf-8")
    res = subprocess.run([PYTHON, "-m", "graphify", "export", "zoom-html", "--graph", str(out_dir / "graph.json"),
                          "--label", "project-a"], cwd=tmp_path, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    html = (out_dir / "GRAPH_ZOOM.html").read_text(encoding="utf-8")
    assert '"project":"project-a"' in html
    usage = subprocess.run([PYTHON, "-m", "graphify", "--help"], cwd=tmp_path, capture_output=True, text=True)
    assert usage.returncode == 0 and "export zoom-html" in usage.stdout
    bare = subprocess.run([PYTHON, "-m", "graphify", "export"], cwd=tmp_path, capture_output=True, text=True)
    assert "zoom-html [--graph PATH] [--output HTML] [--label NAME]" in bare.stderr


def test_empty_graph_still_writes_a_page():
    data = build_zoom_data({"nodes": [], "links": []})
    assert data["classes"] == [] and data["domains"] == [] and data["root"] is None
