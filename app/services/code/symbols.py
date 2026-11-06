"""Text patterns for finding where a symbol is defined and where it is called.

Heuristics that work across languages without a parser; tree-sitter outlines are used
where exact ranges matter.
"""

import re


def definition_pattern(name: str) -> re.Pattern[str]:
    """Regex matching common definition syntaxes across languages (heuristic, not a parser)."""
    n = re.escape(name)
    return re.compile(
        "|".join([
            # Python, JS/TS, Go, Rust, PHP, Kotlin, Swift, Ruby, C#/Java types
            rf"\b(?:def|function|func|fn|fun|sub|class|interface|struct|enum|trait|type|record)\s+{n}\b",
            # Go methods: func (r *Repo) Name(
            rf"\bfunc\s*\([^)]*\)\s*{n}\s*\(",
            # JS/TS: const name = (...) => / const name = function
            rf"\b(?:const|let|var)\s+{n}\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)",
            # Java/C#/PHP/C++ methods with a modifier: public static int name(
            rf"\b(?:public|private|protected|internal|static|override|virtual|final)\b[^;=(]*\b{n}\s*\(",
        ])
    )


def call_pattern(name: str) -> re.Pattern[str]:
    """A call or reference followed by an argument list: ``name(``, ``obj.name(``, ``new Name(``."""
    return re.compile(rf"(?<![\w$]){re.escape(name)}\s*\(")
