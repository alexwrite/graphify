"""PHP static calls `Foo::bar()` bind to the method, not the class (#23).

The extractor used the scope text as the callee, so `Registry::alpha()` linked
the caller to the `Registry` class node and `alpha()` never had a caller. The
scope is now a receiver typed by the class it names, resolved by the same
`use`-aware rules as `(new Registry())->alpha()`.
"""
from __future__ import annotations

from pathlib import Path

from tests.test_php_member_calls import _calls, _find, _full_then_incremental

_REGISTRY = (
    "<?php\nnamespace App\\Support;\n"
    "class Registry {\n"
    "    public static function alpha(): string { return 'a'; }\n"
    "    public static function beta(): string { return 'b'; }\n"
    "}\n"
)
# Same method name in another class: a bare method-name match could pick it.
_DECOY = (
    "<?php\nnamespace App\\Audit;\n"
    "class Ledger {\n    public static function alpha(): string { return 'x'; }\n}\n"
)
_CORPUS = {"app/Support/Registry.php": _REGISTRY, "app/Audit/Ledger.php": _DECOY}


def _caller(body: str, uses: str = "use App\\Support\\Registry;\n") -> str:
    return (
        "<?php\nnamespace App\\Http;\n"
        f"{uses}"
        "class Caller {\n"
        f"    public function run(): mixed {{ {body} }}\n"
        "}\n"
    )


def _run_targets(r: dict, calls: dict) -> set[str]:
    run = _find(r, ".run()", "caller")
    return {tgt for src, tgt in calls if src == run}


def test_static_call_binds_the_method_not_the_class(tmp_path: Path):
    calls, r = _calls(tmp_path, {
        **_CORPUS,
        "app/Http/Caller.php": _caller("return Registry::alpha();"),
    })

    run = _find(r, ".run()", "caller")
    alpha = _find(r, ".alpha()", "registry")
    assert (run, alpha) in calls
    assert (run, _find(r, "Registry", "registry")) not in calls
    assert (run, _find(r, ".alpha()", "ledger")) not in calls
    assert (run, _find(r, ".beta()", "registry")) not in calls
    # The `use` import names the class: the evidence the old class edge had.
    assert calls[(run, alpha)]["confidence"] == "EXTRACTED"


def test_unimported_static_call_is_inferred(tmp_path: Path):
    """Same namespace, no `use`: bound through the single-definition guard."""
    calls, r = _calls(tmp_path, {
        **_CORPUS,
        "app/Support/Caller.php": (
            "<?php\nnamespace App\\Support;\n"
            "class Caller {\n    public function run(): string { return Registry::alpha(); }\n}\n"
        ),
    })

    edge = calls[(_find(r, ".run()", "caller"), _find(r, ".alpha()", "registry"))]
    assert edge["confidence"] == "INFERRED"
    assert edge["confidence_score"] == 0.85


def test_fully_qualified_static_call_is_extracted(tmp_path: Path):
    calls, r = _calls(tmp_path, {
        **_CORPUS,
        "app/Http/Caller.php": _caller("return \\App\\Support\\Registry::alpha();", uses=""),
    })

    edge = calls[(_find(r, ".run()", "caller"), _find(r, ".alpha()", "registry"))]
    assert edge["confidence"] == "EXTRACTED"
    assert edge["confidence_score"] == 1.0


def test_same_file_static_call_binds_the_method(tmp_path: Path):
    calls, r = _calls(tmp_path, {
        "app/Audit/Ledger.php": _DECOY,
        "app/Http/Caller.php": (
            "<?php\nnamespace App\\Http;\n"
            "class Helper {\n    public static function alpha(): string { return 'h'; }\n}\n"
            "class Caller {\n    public function run(): string { return Helper::alpha(); }\n}\n"
        ),
    })

    run = _find(r, ".run()", "caller")
    assert calls[(run, _find(r, ".alpha()", "helper"))]["confidence"] == "EXTRACTED"
    assert (run, _find(r, "Helper", "helper")) not in calls
    assert (run, _find(r, ".alpha()", "ledger")) not in calls


def test_undeclared_static_method_keeps_the_class_edge(tmp_path: Path):
    """`Foo::where()` reaches an inherited or magic method: the class it names
    keeps its edge, and no same-named method of another class gets one."""
    calls, r = _calls(tmp_path, {
        **_CORPUS,
        "app/Audit/Query.php": (
            "<?php\nnamespace App\\Audit;\n"
            "class Query {\n    public static function where(): void {}\n}\n"
        ),
        "app/Http/Caller.php": _caller("return Registry::where();"),
    })

    targets = _run_targets(r, calls)
    assert _find(r, "Registry", "registry") in targets
    assert _find(r, ".where()", "query") not in targets


def test_static_call_on_vendor_import_never_binds_a_namesake(tmp_path: Path):
    """A `use` that names an out-of-corpus class claims the short name: the
    in-corpus class sharing it gets no edge, neither class nor method."""
    calls, r = _calls(tmp_path, {
        "app/Segment/Event.php": (
            "<?php\nnamespace App\\Segment;\n"
            "class Event {\n    public static function listen(): void {}\n}\n"
        ),
        "app/Http/Caller.php": _caller(
            "Event::listen('x', 'y'); return null;",
            uses="use Illuminate\\Support\\Facades\\Event;\n",
        ),
    })

    targets = _run_targets(r, calls)
    assert _find(r, ".listen()", "segment") not in targets
    assert _find(r, "Event", "segment") not in targets


def test_qualified_vendor_static_call_never_binds_a_namesake(tmp_path: Path):
    calls, r = _calls(tmp_path, {
        "app/Segment/Event.php": (
            "<?php\nnamespace App\\Segment;\n"
            "class Event {\n    public static function listen(): void {}\n}\n"
        ),
        "app/Http/Caller.php": _caller(
            "\\Illuminate\\Support\\Facades\\Event::listen('x', 'y'); return null;", uses="",
        ),
    })

    targets = _run_targets(r, calls)
    assert _find(r, ".listen()", "segment") not in targets
    assert _find(r, "Event", "segment") not in targets


def test_static_call_on_ambiguous_short_name_is_refused(tmp_path: Path):
    calls, r = _calls(tmp_path, {
        "app/A/Tool.php": "<?php\nnamespace App\\A;\nclass Tool {\n    public static function go(): void {}\n}\n",
        "app/B/Tool.php": "<?php\nnamespace App\\B;\nclass Tool {\n    public static function go(): void {}\n}\n",
        "app/Http/Caller.php": _caller("Tool::go(); return null;", uses=""),
    })

    assert not any("tool" in tgt for tgt in _run_targets(r, calls))


def test_dynamic_static_calls_bind_nothing(tmp_path: Path):
    calls, r = _calls(tmp_path, {
        **_CORPUS,
        "app/Http/Caller.php": _caller(
            "$c = Registry::class; $m = 'alpha'; $c::alpha(); return Registry::$m();"
        ),
    })

    assert not any("alpha" in tgt for tgt in _run_targets(r, calls))


def test_static_first_class_callable_is_an_indirect_call(tmp_path: Path):
    _, r = _calls(tmp_path, {
        **_CORPUS,
        "app/Http/Caller.php": _caller("return Registry::alpha(...);"),
    })

    run = _find(r, ".run()", "caller")
    alpha = _find(r, ".alpha()", "registry")
    relations = {e["relation"] for e in r["edges"] if (e["source"], e["target"]) == (run, alpha)}
    assert relations == {"indirect_call"}


def test_static_binding_agrees_across_an_incremental_rebuild(tmp_path: Path):
    caller = "app/Http/Caller.php"
    (full_calls, full), (inc_calls, inc) = _full_then_incremental(tmp_path, {
        **_CORPUS,
        caller: _caller("return Registry::alpha();"),
    }, changed=caller)

    alpha = _find(full, ".alpha()", "registry")
    full_edge = full_calls[(_find(full, ".run()", "caller"), alpha)]
    inc_edge = inc_calls[(_find(inc, ".run()", "caller"), alpha)]
    assert inc_edge["confidence"] == full_edge["confidence"] == "EXTRACTED"
