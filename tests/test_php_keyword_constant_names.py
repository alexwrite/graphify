"""PHP class constants named after reserved keywords must parse (#17).

PHP allows any keyword as a class constant name (``public const string DEFAULT``).
tree-sitter-php up to 0.25.1 rejects most of them once the constant is typed
(PHP 8.3), and a handful (``VOID``, ``TRUE``...) even untyped
(tree-sitter/tree-sitter-php#295). Error recovery then swallows the whole class
declaration: the class vanishes from the graph and every ``Foo::DEFAULT`` access
loses its ``references_constant`` edge.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from graphify.extract import extract, extract_php

_CORPUS = """<?php

declare(strict_types=1);

namespace App\\Http;

final class HttpAgent
{
    public const string DEFAULT = 'Fetcher/1.0 (+https://example.org; ops@example.org)';

    public const string CRAWLER = 'Fetcher/1.0 (Crawler; +https://example.org; ops@example.org)';
}

final class Client
{
    public function agent(): string
    {
        return HttpAgent::DEFAULT;
    }
}
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _node_id(result: dict, label: str) -> str | None:
    return next(
        (n["id"] for n in result["nodes"] if n.get("label") == label and n.get("source_file")),
        None,
    )


@pytest.mark.parametrize(
    "declaration",
    [
        "public const string DEFAULT = 'a';",
        "final protected const int PRIVATE = 1;",
        "const ?string CLASS = null;",
        "public const string|int FUNCTION = 2;",
        "public const string NEW = 'b';",
        "const VOID = 3;",
        "const TRUE = true;",
    ],
)
def test_keyword_named_constant_parses_cleanly(tmp_path: Path, declaration: str) -> None:
    src = _write(tmp_path / "Holder.php", f"<?php\nfinal class Holder\n{{\n    {declaration}\n}}\n")

    result = extract_php(src)

    assert "parse_errors" not in result
    assert any(n.get("label") == "Holder" for n in result["nodes"])


def test_constants_only_class_keeps_its_node_and_fan_in(tmp_path: Path) -> None:
    src = _write(tmp_path / "src/Http/HttpAgent.php", _CORPUS)

    result = extract([src], cache_root=tmp_path / "graphify-out")

    agent_id = _node_id(result, "HttpAgent")
    assert agent_id is not None, "HttpAgent declaration node is missing"
    assert any(
        e.get("relation") == "references_constant" and e["target"] == agent_id
        for e in result["edges"]
    ), "Client::agent() lost its references_constant edge to HttpAgent"
