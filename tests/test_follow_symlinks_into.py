"""Out-of-root symlinked directories are indexed only under a declared trusted root.

A host project that loads plugins through symlinks (``addons/<name> ->
../../Addons/<repo>/addons/<name>``) used to contribute zero nodes for them: the
containment rule drops every target that resolves outside the scan root, and no
option could lift it. ``follow_symlinks_into`` (CLI ``--follow-symlinks-into``,
env ``GRAPHIFY_FOLLOW_SYMLINKS_INTO``) admits targets under an allowlist only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from graphify.detect import FOLLOW_SYMLINKS_INTO_ENV, detect

PYTHON = sys.executable

CORE_PHP = """<?php
namespace App;
use Addons\\Foo\\FooService;
class Core {
    public function run(): void { $s = new FooService(); $s->work(); }
    public function helper(): int { return 1; }
}
"""

ADDON_PHP = """<?php
namespace Addons\\Foo;
use App\\Core;
class FooService {
    public function work(): int { $c = new Core(); return $c->helper(); }
}
"""


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    """Host app plus an addon repository beside it, linked the ClientXCMS way."""
    app = tmp_path / "ws" / "app"
    (app / "src").mkdir(parents=True)
    (app / "addons").mkdir()
    (app / "src" / "Core.php").write_text(CORE_PHP)
    addons_repo = tmp_path / "ws" / "Addons"
    addon_src = addons_repo / "repo1" / "addons" / "foo" / "src"
    addon_src.mkdir(parents=True)
    (addon_src / "FooService.php").write_text(ADDON_PHP)
    (app / "addons" / "foo").symlink_to(Path("../../Addons/repo1/addons/foo"))
    return app, addons_repo


def _code(result: dict, root: Path) -> list[str]:
    return sorted(Path(p).relative_to(root).as_posix() for p in result["files"]["code"])


def test_default_still_skips_out_of_root_symlinked_dir(requires_symlinks, tmp_path):
    app, _ = _workspace(tmp_path)

    result = detect(app)

    assert _code(result, app) == ["src/Core.php"]


def test_follow_symlinks_into_indexes_linked_dir_under_link_spelling(requires_symlinks, tmp_path):
    app, addons_repo = _workspace(tmp_path)

    result = detect(app, follow_symlinks_into=[addons_repo])

    assert _code(result, app) == ["addons/foo/src/FooService.php", "src/Core.php"]


def test_target_outside_every_trusted_root_is_still_refused(requires_symlinks, tmp_path):
    app, addons_repo = _workspace(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "leak.php").write_text("<?php class Leak {}\n")
    (app / "addons" / "leak").symlink_to(elsewhere)
    (addons_repo / "repo1" / "addons" / "foo" / "src" / "Leak.php").symlink_to(elsewhere / "leak.php")

    result = detect(app, follow_symlinks_into=[addons_repo])

    assert _code(result, app) == ["addons/foo/src/FooService.php", "src/Core.php"]
    skipped = "\n".join(result["skipped_sensitive"])
    assert "addons/leak [symlink target outside scan root]" in Path(skipped).as_posix()
    assert "Leak.php [symlink target outside scan root]" in skipped


def test_two_links_to_one_target_index_it_once(requires_symlinks, tmp_path):
    app, addons_repo = _workspace(tmp_path)
    (app / "addons" / "foo_alias").symlink_to(Path("../../Addons/repo1/addons/foo"))
    (app / "src" / "Alias.php").symlink_to(addons_repo / "repo1" / "addons" / "foo" / "src" / "FooService.php")

    result = detect(app, follow_symlinks_into=[addons_repo])

    assert _code(result, app) == ["addons/foo/src/FooService.php", "src/Core.php"]
    assert any("[duplicate symlink target]" in s for s in result["skipped_sensitive"])


def test_cyclic_links_between_trusted_dirs_terminate(requires_symlinks, tmp_path):
    app, addons_repo = _workspace(tmp_path)
    a = addons_repo / "cycle" / "a"
    b = addons_repo / "cycle" / "b"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "A.php").write_text("<?php class A {}\n")
    (b / "B.php").write_text("<?php class B {}\n")
    (a / "to_b").symlink_to(b)
    (b / "to_a").symlink_to(a)
    (a / "to_self").symlink_to(a)
    (app / "addons" / "cycle").symlink_to(a)

    result = detect(app, follow_symlinks_into=[addons_repo])

    assert _code(result, app) == [
        "addons/cycle/A.php",
        "addons/cycle/to_b/B.php",
        "addons/foo/src/FooService.php",
        "src/Core.php",
    ]
    assert result["walk_errors"] == []
    duplicates = sorted(
        Path(s.split(" [")[0]).relative_to(app).as_posix()
        for s in result["skipped_sensitive"]
        if s.endswith("[duplicate symlink target]")
    )
    assert duplicates == ["addons/cycle/to_b/to_a", "addons/cycle/to_self"]


def test_host_gitignore_does_not_hide_a_trusted_link_but_graphifyignore_does(requires_symlinks, tmp_path):
    app, addons_repo = _workspace(tmp_path)
    # The host keeps addons out of git; that says nothing about the graph.
    (app / "addons" / ".gitignore").write_text("*\n")
    addon = addons_repo / "repo1" / "addons" / "foo"
    (addon / ".gitignore").write_text("generated/\n")
    (addon / "generated").mkdir()
    (addon / "generated" / "Cache.php").write_text("<?php class Cache {}\n")
    (addons_repo / "repo2").mkdir()
    (addons_repo / "repo2" / "Bar.php").write_text("<?php class Bar {}\n")
    (app / "addons" / "bar").symlink_to(addons_repo / "repo2")
    (app / ".graphifyignore").write_text("addons/bar/\n")

    result = detect(app, follow_symlinks_into=[addons_repo])

    assert _code(result, app) == ["addons/foo/src/FooService.php", "src/Core.php"]


def test_env_var_declares_trusted_roots(requires_symlinks, tmp_path, monkeypatch, capsys):
    app, addons_repo = _workspace(tmp_path)
    monkeypatch.setenv(
        FOLLOW_SYMLINKS_INTO_ENV,
        os.pathsep.join(["relative/dir", str(tmp_path / "missing"), str(addons_repo)]),
    )

    result = detect(app)

    assert _code(result, app) == ["addons/foo/src/FooService.php", "src/Core.php"]
    err = capsys.readouterr().err
    assert "relative/dir" in err and "missing" in err


def test_filesystem_root_is_never_a_trusted_root(requires_symlinks, tmp_path, capsys):
    app, _ = _workspace(tmp_path)

    result = detect(app, follow_symlinks_into=[Path(Path(tmp_path).anchor)])

    assert _code(result, app) == ["src/Core.php"]
    assert "filesystem root" in capsys.readouterr().err


def _run_cli(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != FOLLOW_SYMLINKS_INTO_ENV}
    env["GRAPHIFY_QUERY_LOG_DISABLE"] = "1"
    return subprocess.run(
        [PYTHON, "-m", "graphify", *args], cwd=cwd, capture_output=True, text=True, env=env,
    )


def _graph(app: Path) -> dict:
    return json.loads((app / "graphify-out" / "graph.json").read_text())


def _addon_view(graph: dict) -> tuple[set[str], set[tuple[str, str]]]:
    files = {n.get("source_file", "") for n in graph["nodes"]}
    by_id = {n["id"]: n.get("source_file", "") for n in graph["nodes"]}
    calls = {
        (by_id[e["source"]], by_id[e["target"]])
        for e in graph.get("links", graph.get("edges", []))
        if e.get("relation") == "calls"
    }
    return files, calls


def test_extract_flag_persists_for_update(requires_symlinks, tmp_path):
    app, addons_repo = _workspace(tmp_path)

    r = _run_cli(app, "extract", ".", "--code-only", "--no-cluster", "--follow-symlinks-into", str(addons_repo))
    assert r.returncode == 0, r.stderr
    files, calls = _addon_view(_graph(app))
    assert "addons/foo/src/FooService.php" in files
    assert not any(f.startswith("/") or str(addons_repo) in f for f in files)
    assert ("src/Core.php", "addons/foo/src/FooService.php") in calls
    assert ("addons/foo/src/FooService.php", "src/Core.php") in calls

    # The rebuild must re-scan the addon, not merely carry its old nodes forward.
    addon_file = addons_repo / "repo1" / "addons" / "foo" / "src" / "FooService.php"
    addon_file.write_text(ADDON_PHP.replace("class FooService {", "class FooService {\n    public function extra(): void {}"))
    r = _run_cli(app, "update", ".", "--force")
    assert r.returncode == 0, r.stderr
    graph = _graph(app)
    files, calls = _addon_view(graph)
    assert ("src/Core.php", "addons/foo/src/FooService.php") in calls
    assert any(
        n.get("source_file") == "addons/foo/src/FooService.php" and "extra" in str(n.get("label"))
        for n in graph["nodes"]
    )


def test_extract_rejects_a_missing_trusted_root(requires_symlinks, tmp_path):
    app, _ = _workspace(tmp_path)

    r = _run_cli(app, "extract", ".", "--code-only", "--follow-symlinks-into", str(tmp_path / "nope"))

    assert r.returncode != 0
    assert "--follow-symlinks-into" in r.stderr
