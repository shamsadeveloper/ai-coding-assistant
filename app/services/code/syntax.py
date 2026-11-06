"""Source structure via Tree-sitter: symbols (functions, classes, ...) with line ranges, and imports.

Works for every language in ``DEFINITION_NODES``. Other languages return an
outline with ``supported=False`` so callers can fall back to plain text.
"""

import logging
from bisect import bisect_right
from dataclasses import dataclass, field
from functools import lru_cache

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_parser

logger = logging.getLogger(__name__)

# Our language names (app.services.repository.language) -> Tree-sitter grammar names.
GRAMMARS = {
    "Python": "python",
    "JavaScript": "javascript",
    "TypeScript": "typescript",
    "Go": "go",
    "Java": "java",
    "C#": "csharp",
    "PHP": "php",
    "Rust": "rust",
    "Ruby": "ruby",
}

_JS_DEFINITIONS = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "method_definition": "method",
}
_TS_DEFINITIONS = {
    **_JS_DEFINITIONS,
    "abstract_class_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
}

DEFINITION_NODES: dict[str, dict[str, str]] = {
    "python": {"function_definition": "function", "class_definition": "class"},
    "javascript": _JS_DEFINITIONS,
    "typescript": _TS_DEFINITIONS,
    "tsx": _TS_DEFINITIONS,
    "go": {"function_declaration": "function", "method_declaration": "method", "type_spec": "type"},
    "java": {
        "class_declaration": "class", "interface_declaration": "interface", "enum_declaration": "enum",
        "record_declaration": "class", "method_declaration": "method", "constructor_declaration": "constructor",
    },
    "csharp": {
        "class_declaration": "class", "interface_declaration": "interface", "struct_declaration": "struct",
        "enum_declaration": "enum", "record_declaration": "class", "method_declaration": "method",
        "constructor_declaration": "constructor",
    },
    "php": {
        "function_definition": "function", "class_declaration": "class", "interface_declaration": "interface",
        "trait_declaration": "trait", "method_declaration": "method",
    },
    "rust": {
        "function_item": "function", "struct_item": "struct", "enum_item": "enum",
        "trait_item": "trait", "impl_item": "impl",
    },
    "ruby": {"method": "method", "singleton_method": "method", "class": "class", "module": "module"},
}

IMPORT_NODES: dict[str, set[str]] = {
    "python": {"import_statement", "import_from_statement"},
    "javascript": {"import_statement"},
    "typescript": {"import_statement"},
    "tsx": {"import_statement"},
    "go": {"import_spec"},
    "java": {"import_declaration"},
    "csharp": {"using_directive"},
    "php": {"namespace_use_declaration"},
    "rust": {"use_declaration"},
    "ruby": set(),
}

_CONTAINER_KINDS = {"class", "interface", "struct", "trait", "impl", "module"}
_JS_FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
MAX_SYMBOLS = 1000


@dataclass(frozen=True)
class Symbol:
    name: str
    qualified_name: str  # e.g. "Repository.scan" for a method
    kind: str
    start_line: int  # 1-based, inclusive
    end_line: int
    signature: str  # first line of the definition


@dataclass(frozen=True)
class ImportStatement:
    line: int
    text: str
    source: str | None = None  # JS/TS module specifier, e.g. "./utils"


@dataclass
class FileOutline:
    path: str
    language: str | None
    supported: bool
    has_syntax_errors: bool = False
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportStatement] = field(default_factory=list)

    def find(self, name: str) -> Symbol | None:
        """Find by qualified name first ("Class.method"), then by plain name."""
        for symbol in self.symbols:
            if symbol.qualified_name == name:
                return symbol
        return next((s for s in self.symbols if s.name == name), None)


def grammar_for(path: str, language: str | None) -> str | None:
    if path.lower().endswith(".tsx"):
        return "tsx"
    return GRAMMARS.get(language or "")


@lru_cache(maxsize=None)
def _parser(grammar: str) -> Parser:
    return get_parser(grammar)


def parse_outline(path: str, language: str | None, source: str) -> FileOutline:
    """Parse ``source`` and return its symbols and imports."""
    grammar = grammar_for(path, language)
    if grammar is None:
        return FileOutline(path, language, supported=False)

    # Only byte offsets are read from nodes. With tree-sitter 0.26 on Python 3.14/Windows,
    # the Point objects returned by ``Node.start_point``/``end_point`` crash the garbage
    # collector (access violation), so line numbers are computed from offsets instead.
    source_bytes = source.encode("utf-8")
    line_starts = [0, *(i + 1 for i, byte in enumerate(source_bytes) if byte == 0x0A)]

    def line_of(byte_offset: int) -> int:
        return bisect_right(line_starts, byte_offset)  # 1-based

    def end_line_of(node: Node) -> int:
        # end_byte is exclusive; a node ending right after "\n" ends on the previous line.
        return line_of(max(node.start_byte, node.end_byte - 1))

    def text_of(node: Node | None) -> str:
        if node is None:
            return ""
        return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def first_line(node: Node) -> str:
        lines = text_of(node).splitlines()
        return lines[0].strip()[:160] if lines else ""

    def symbol_name(node: Node) -> str:
        field_name = "type" if node.type == "impl_item" else "name"
        return text_of(node.child_by_field_name(field_name)) or "<anonymous>"

    try:
        tree = _parser(grammar).parse(source_bytes)
    except Exception as exc:  # grammar download or native parser failure
        logger.warning("Tree-sitter parser for %s unavailable: %s", grammar, exc)
        return FileOutline(path, language, supported=False)

    outline = FileOutline(path, language, supported=True, has_syntax_errors=tree.root_node.has_error)
    definitions = DEFINITION_NODES[grammar]
    import_types = IMPORT_NODES[grammar]

    def visit(node: Node, parent: str | None, parent_kind: str | None) -> None:
        if len(outline.symbols) >= MAX_SYMBOLS:
            return
        if node.type in import_types:
            source_node = node.child_by_field_name("source")
            outline.imports.append(
                ImportStatement(
                    line_of(node.start_byte),
                    " ".join(text_of(node).split())[:300],
                    text_of(source_node).strip("'\"`") or None,
                )
            )
            return

        kind = definitions.get(node.type)
        if kind is None and node.type == "variable_declarator" and grammar in {"javascript", "typescript", "tsx"}:
            value = node.child_by_field_name("value")
            if value is not None and value.type in _JS_FUNCTION_VALUES:
                kind = "function"
        if kind is None:
            for child in node.children:
                visit(child, parent, parent_kind)
            return

        if kind == "function" and parent_kind in _CONTAINER_KINDS:
            kind = "method"
        name = symbol_name(node)
        qualified = f"{parent}.{name}" if parent else name
        # Report the whole statement for JS "const f = () => {}" so the range covers the body.
        span = node.parent if node.type == "variable_declarator" and node.parent is not None else node
        outline.symbols.append(
            Symbol(name, qualified, kind, line_of(span.start_byte), end_line_of(span), first_line(span))
        )
        for child in node.children:
            visit(child, qualified, kind)

    visit(tree.root_node, None, None)
    return outline
