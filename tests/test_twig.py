"""Tests for the Twig template extractor (graphify/extractors/twig.py)."""
from __future__ import annotations

from pathlib import Path

from graphify.extract import extract_twig


def _write(root: Path, rel: str, body: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _rel_targets(r, relation: str) -> set[str]:
    """Readable name of every target reached by *relation*.

    A reference that resolved to another file has no node here (that file owns
    its own), so its name comes from the edge's ``target_file`` stamp; an
    unresolved one is named by its stub node.
    """
    lab = {n["id"]: n["label"] for n in r["nodes"]}
    out = set()
    for e in r["edges"]:
        if e["relation"] != relation:
            continue
        if "target_file" in e:
            out.add(Path(e["target_file"]).name)
        else:
            out.add(lab.get(e["target"], e["target"]))
    return out


def _target_files(r, relation: str) -> set[str]:
    return {e["target_file"] for e in r["edges"]
            if e["relation"] == relation and "target_file" in e}


def _theme(tmp_path: Path) -> Path:
    """A minimal Drupal theme: info.yml root plus two SDC components."""
    root = tmp_path / "mytheme"
    _write(root, "mytheme.info.yml", "name: My Theme\ntype: theme\n")
    _write(root, "components/atoms/icon/icon.twig", "<i></i>\n")
    _write(root, "components/molecules/card/card.twig", "<article></article>\n")
    return root


# --- Drupal SDC dialect -----------------------------------------------------

def test_sdc_include_resolves_to_the_component_template(tmp_path):
    root = _theme(tmp_path)
    page = _write(root, "templates/page.html.twig",
                  "{% include 'mytheme:card' with { title: 'x' } only %}\n")

    r = extract_twig(page)

    assert _rel_targets(r, "includes") == {"card.twig"}
    # Resolved to the real file, not a stub keyed on the raw 'mytheme:card'.
    assert _target_files(r, "includes") == {
        str((root / "components/molecules/card/card.twig").resolve())
    }


def test_sdc_embed_and_extends_get_their_own_relations(tmp_path):
    root = _theme(tmp_path)
    page = _write(root, "templates/page.html.twig",
                  "{% extends 'mytheme:card' %}\n"
                  "{% embed 'mytheme:icon' %}{% endembed %}\n")

    r = extract_twig(page)

    assert _rel_targets(r, "extends") == {"card.twig"}
    assert _rel_targets(r, "embeds") == {"icon.twig"}


def test_unknown_component_still_emits_an_edge_to_a_stub(tmp_path):
    root = _theme(tmp_path)
    page = _write(root, "templates/page.html.twig",
                  "{% include 'mytheme:nope' %}\n")

    r = extract_twig(page)

    # The include is a fact of the source even when the target is not in the
    # corpus, so the edge stays and the raw reference becomes the label.
    assert _rel_targets(r, "includes") == {"mytheme:nope"}


def test_include_of_a_foreign_provider_is_not_resolved_locally(tmp_path):
    root = _theme(tmp_path)
    # `othertheme` has no othertheme.info.yml anywhere up the tree, so the
    # component index of *this* theme must not be consulted for it.
    page = _write(root, "templates/page.html.twig",
                  "{% include 'othertheme:card' %}\n")

    r = extract_twig(page)

    assert _rel_targets(r, "includes") == {"othertheme:card"}


# --- plain-path / Symfony dialect -------------------------------------------

def test_path_reference_resolves_against_the_templates_root(tmp_path):
    root = tmp_path / "app"
    _write(root, "templates/layout.html.twig", "<html></html>\n")
    page = _write(root, "templates/pages/home.html.twig",
                  "{% extends 'layout.html.twig' %}\n")

    r = extract_twig(page)

    assert _rel_targets(r, "extends") == {"layout.html.twig"}
    assert _target_files(r, "extends") == {
        str((root / "templates/layout.html.twig").resolve())
    }


def test_bare_name_ignores_a_same_named_file_beside_the_referrer(tmp_path):
    root = tmp_path / "app"
    _write(root, "templates/base.html.twig", "<html></html>\n")
    _write(root, "templates/admin/base.html.twig", "<div></div>\n")
    page = _write(root, "templates/admin/page.html.twig",
                  "{% extends 'base.html.twig' %}\n")

    r = extract_twig(page)

    # Twig names are relative to the loader root, never to the current file.
    assert _target_files(r, "extends") == {
        str((root / "templates/base.html.twig").resolve())
    }


def test_nested_templates_dir_is_not_taken_for_the_loader_root(tmp_path):
    root = tmp_path / "app"
    _write(root, "templates/layout.html.twig", "<html></html>\n")
    _write(root, "templates/docs/templates/layout.html.twig", "<p></p>\n")
    page = _write(root, "templates/docs/page.html.twig",
                  "{% extends 'layout.html.twig' %}\n")

    r = extract_twig(page)

    assert _target_files(r, "extends") == {
        str((root / "templates/layout.html.twig").resolve())
    }


def test_bundle_namespace_is_not_resolved_against_the_app_templates(tmp_path):
    root = tmp_path / "app"
    _write(root, "templates/parts/nav.html.twig", "<nav></nav>\n")
    page = _write(root, "templates/home.html.twig",
                  "{% include '@Shared/parts/nav.html.twig' %}\n")

    r = extract_twig(page)

    # A Symfony namespace maps to a bundle or a configured path the template
    # cannot see, so the reference stays a stub instead of a guessed file.
    assert _target_files(r, "includes") == set()
    assert _rel_targets(r, "includes") == {"@Shared/parts/nav.html.twig"}


def test_drupal_namespace_resolves_under_the_extension_templates_dir(tmp_path):
    root = _theme(tmp_path)
    _write(root, "templates/parts/nav.html.twig", "<nav></nav>\n")
    page = _write(root, "templates/page.html.twig",
                  "{% include '@mytheme/parts/nav.html.twig' %}\n")

    r = extract_twig(page)

    assert _target_files(r, "includes") == {
        str((root / "templates/parts/nav.html.twig").resolve())
    }


def test_foreign_drupal_namespace_does_not_link_to_the_local_override(tmp_path):
    sub = tmp_path / "subtheme"
    _write(sub, "subtheme.info.yml", "name: Sub\ntype: theme\n")
    page = _write(sub, "templates/node.html.twig",
                  "{% extends '@basetheme/node.html.twig' %}\n")

    r = extract_twig(page)

    # The base theme is not under this file's tree: no self-loop to the override.
    assert _target_files(r, "extends") == set()


def test_include_function_form_is_extracted(tmp_path):
    root = _theme(tmp_path)
    page = _write(root, "templates/page.html.twig",
                  "{{ include('mytheme:icon') }}\n")

    r = extract_twig(page)

    assert _rel_targets(r, "includes") == {"icon.twig"}


# --- comments ---------------------------------------------------------------

def test_usage_examples_in_docblocks_are_not_dependencies(tmp_path):
    """A component's own docblock routinely shows how to include it.

    Reading that as a real tag gave the component an include edge to itself,
    which is both wrong and a self-loop in the built graph.
    """
    root = _theme(tmp_path)
    card = root / "components/molecules/card/card.twig"
    card.write_text(
        "{#\n"
        " * Card component.\n"
        " *\n"
        " * Usage:\n"
        " *   {% include 'mytheme:card' with { title: 'Hello' } %}\n"
        " #}\n"
        "<article>{% include 'mytheme:icon' %}</article>\n",
        encoding="utf-8",
    )

    r = extract_twig(card)

    assert _rel_targets(r, "includes") == {"icon.twig"}
    file_nid = r["nodes"][0]["id"]
    assert not [e for e in r["edges"] if e["target"] == file_nid], "self-loop"


def test_comment_stripping_preserves_line_numbers(tmp_path):
    root = _theme(tmp_path)
    page = _write(root, "templates/page.html.twig",
                  "{# a\nmultiline\ncomment #}\n"
                  "{% include 'mytheme:icon' %}\n")

    r = extract_twig(page)

    location = next(e["source_location"] for e in r["edges"]
                    if e["relation"] == "includes")
    assert location == "L4"


# --- misc -------------------------------------------------------------------

def test_repeated_include_of_one_component_is_a_single_edge(tmp_path):
    root = _theme(tmp_path)
    page = _write(root, "templates/page.html.twig",
                  "{% include 'mytheme:card' %}\n"
                  "{% include 'mytheme:card' %}\n")

    r = extract_twig(page)

    assert len([e for e in r["edges"] if e["relation"] == "includes"]) == 1


def test_blocks_and_attached_libraries(tmp_path):
    root = _theme(tmp_path)
    page = _write(root, "templates/page.html.twig",
                  "{{ attach_library('mytheme/global') }}\n"
                  "{% block content %}{% endblock %}\n")

    r = extract_twig(page)

    assert _rel_targets(r, "defines_block") == {"content"}
    assert _rel_targets(r, "attaches_library") == {"mytheme/global"}


def test_every_edge_is_extracted_with_full_confidence(tmp_path):
    root = _theme(tmp_path)
    page = _write(root, "templates/page.html.twig",
                  "{% include 'mytheme:card' %}\n")

    r = extract_twig(page)

    assert r["edges"], "expected at least one edge"
    for e in r["edges"]:
        assert e["confidence"] == "EXTRACTED"
        assert e["confidence_score"] == 1.0


def test_resolved_reference_stamps_target_file(tmp_path):
    """The stamp is what lets the target canonicalize to the real file node.

    Without it the target keeps an absolute-path-derived id that matches no node
    in the merged graph, and every template->template edge silently drops
    (#2211, the same failure fixed for Python imports and markdown refs).
    """
    root = _theme(tmp_path)
    page = _write(root, "templates/page.html.twig",
                  "{% include 'mytheme:card' %}\n")

    r = extract_twig(page)

    edge = next(e for e in r["edges"] if e["relation"] == "includes")
    assert edge["target_file"] == str(
        (root / "components/molecules/card/card.twig").resolve())


def test_unresolved_reference_is_left_dangling(tmp_path):
    root = _theme(tmp_path)
    page = _write(root, "templates/page.html.twig",
                  "{% include 'mytheme:nope' %}\n")

    r = extract_twig(page)

    edge = next(e for e in r["edges"] if e["relation"] == "includes")
    # No file to canonicalize onto, so no stamp — mirrors markdown's
    # existence-gated behavior for links to nonexistent docs.
    assert "target_file" not in edge


def test_unreadable_file_reports_an_error(tmp_path):
    assert "error" in extract_twig(tmp_path / "missing.twig")


def test_php_referrer_under_a_dir_named_templates_finds_the_app_loader_root(tmp_path):
    from graphify.extractors.twig import _resolve_path_ref

    app = tmp_path / "templates" / "app"
    _write(app, "templates/a.html.twig", "<p></p>\n")
    controller = _write(app, "src/Controller/A.php", "<?php\n")

    # Only a template's own tree is a loader root; a PHP file walks up to `templates/`.
    assert _resolve_path_ref("a.html.twig", controller) == app / "templates/a.html.twig"
