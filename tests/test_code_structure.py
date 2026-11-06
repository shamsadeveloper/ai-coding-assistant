"""Tree-sitter outlines, import resolution and duplicate detection (no LLM)."""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.services.code.dependencies import build_dependency_graph
from app.services.code.duplicates import find_duplicates
from app.services.code.syntax import parse_outline
from app.services.intelligence.service import chunk_ranges

PYTHON_SOURCE = textwrap.dedent('''\
    import os
    from app.utils import add

    class Greeter(Base):
        """Says hello."""

        def greet(self, name):
            return f"hi {name}"

        async def close(self):
            pass

    def main():
        text = "def fake_in_string(): pass"
        return Greeter().greet(text)
''')


def symbols(outline):
    return [(s.kind, s.qualified_name, s.start_line, s.end_line) for s in outline.symbols]


def test_python_outline():
    outline = parse_outline("app/greeter.py", "Python", PYTHON_SOURCE)

    assert outline.supported and not outline.has_syntax_errors
    assert symbols(outline) == [
        ("class", "Greeter", 4, 11),
        ("method", "Greeter.greet", 7, 8),
        ("method", "Greeter.close", 10, 11),
        ("function", "main", 13, 15),
    ]
    assert [i.line for i in outline.imports] == [1, 2]
    assert outline.find("greet").qualified_name == "Greeter.greet"
    assert outline.find("Greeter.close").start_line == 10


@pytest.mark.parametrize(
    ("path", "language", "source", "expected"),
    [
        (
            "src/a.ts", "TypeScript",
            "export const add = (a: number, b: number) => {\n  return a + b;\n};\n"
            "interface Shape { area(): number }\nclass Box { size() { return 1 } }\n",
            [("function", "add"), ("interface", "Shape"), ("class", "Box"), ("method", "Box.size")],
        ),
        (
            "main.go", "Go",
            "package main\nfunc Add(a int) int { return a }\ntype Repo struct{}\nfunc (r *Repo) Save() error { return nil }\n",
            [("function", "Add"), ("type", "Repo"), ("method", "Save")],
        ),
        (
            "A.java", "Java",
            "class Account {\n  Account() {}\n  public int balance() { return 0; }\n}\n",
            [("class", "Account"), ("constructor", "Account.Account"), ("method", "Account.balance")],
        ),
        (
            "U.php", "PHP",
            "<?php\nclass UserService {\n  public function find($id) { return null; }\n}\nfunction helper() {}\n",
            [("class", "UserService"), ("method", "UserService.find"), ("function", "helper")],
        ),
    ],
)
def test_outline_other_languages(path, language, source, expected):
    outline = parse_outline(path, language, source)

    assert [(s.kind, s.qualified_name) for s in outline.symbols] == expected


def test_unsupported_language_returns_empty_outline():
    outline = parse_outline("notes.md", "Markdown", "# hi")

    assert outline.supported is False
    assert outline.symbols == []


def test_dependency_graph_python_and_typescript():
    files = {
        "app/__init__.py": "",
        "app/main.py": "from app.services import llm\nfrom .config import settings\nimport os\n",
        "app/config.py": "settings = 1\n",
        "app/services/__init__.py": "",
        "app/services/llm.py": "import httpx\n",
        "web/index.ts": "import { x } from './util';\nimport React from 'react';\n",
        "web/util.ts": "export const x = 1;\n",
    }
    language = {".py": "Python", ".ts": "TypeScript"}
    outlines = [parse_outline(p, language[Path(p).suffix], t) for p, t in files.items()]

    graph = build_dependency_graph(outlines, set(files))

    assert graph["app/main.py"].imports == ["app/config.py", "app/services/__init__.py", "app/services/llm.py"]
    assert graph["app/main.py"].external == ["os"]
    assert graph["app/services/llm.py"].imported_by == ["app/main.py"]
    assert graph["web/index.ts"].imports == ["web/util.ts"]
    assert graph["web/index.ts"].external == ["react"]
    assert graph["web/util.ts"].imported_by == ["web/index.ts"]


DUPLICATED = textwrap.dedent("""\
    def total(items):
        result = 0
        for item in items:
            if item.price is not None and item.quantity > 0:
                result += item.price * item.quantity
        discount = calculate_discount(result)
        return result - discount
""")


def test_find_duplicates_across_files_ignoring_whitespace_and_comments():
    other = "# copy\n\n" + DUPLICATED.replace("    ", "  ").replace("result = 0", "result = 0  ")
    blocks = find_duplicates([("a.py", DUPLICATED), ("b.py", other), ("c.py", "x = 1\n")], min_lines=6)

    assert len(blocks) == 1
    assert blocks[0].line_count == 7
    assert [(loc.path, loc.start_line, loc.end_line) for loc in blocks[0].locations] == [
        ("a.py", 1, 7), ("b.py", 3, 9),
    ]


def test_find_duplicates_ignores_short_trivial_blocks():
    trivial = "}\n}\n}\n}\n}\n}\n"
    assert find_duplicates([("a.js", trivial), ("b.js", trivial)], min_lines=6) == []


def test_chunk_ranges_cut_at_symbol_boundaries():
    source = "\n".join(f"def f{i}():\n    return {i}\n" for i in range(100))  # 300 lines
    outline = parse_outline("big.py", "Python", source)

    ranges = chunk_ranges(outline, 300, max_lines=100)

    assert ranges[0][0] == 1 and ranges[-1][1] == 300
    assert all(end - start + 1 <= 100 for start, end in ranges)
    assert all(end + 1 == start for (_, end), (start, _) in zip(ranges, ranges[1:]))


def test_parsing_many_files_survives_garbage_collection():
    """Regression: reading Node.start_point crashed the GC (tree-sitter 0.26, Windows)."""
    script = (
        "import gc\nfrom pathlib import Path\nfrom app.services.code.syntax import parse_outline\n"
        "for _ in range(2):\n"
        "    for p in sorted(Path('app').rglob('*.py')):\n"
        "        parse_outline(p.as_posix(), 'Python', p.read_text(encoding='utf-8'))\n"
        "        gc.collect()\n"
        "print('ok')\n"
    )
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-c", script], cwd=root, capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, result.stderr[-500:]
    assert result.stdout.strip() == "ok"
