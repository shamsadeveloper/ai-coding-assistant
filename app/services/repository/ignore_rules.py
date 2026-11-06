"""Which directories and files the scanner skips: defaults, secrets, and .gitignore rules."""

from fnmatch import fnmatchcase
from pathlib import Path

from pathspec import GitIgnoreSpec

DEFAULT_EXCLUDED_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", "bower_components", "vendor",
    ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    "build", "dist", "out", "target", "obj", "coverage", ".next", ".nuxt", ".gradle",
    ".idea", ".vscode",
})

SENSITIVE_FILE_PATTERNS = (
    ".env", ".env.*", "*.env",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.kdbx",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
    ".npmrc", ".pypirc", ".netrc", ".git-credentials", ".htpasswd",
    "credentials.json", "secrets.*", "*.tfstate", "*.tfvars",
)

# Templates that document variable names without real values.
SAFE_ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template", ".env.dist"})


def is_sensitive_file(file_name: str) -> bool:
    """Return True for files that commonly hold secrets and must never be read."""
    name = file_name.lower()
    if name in SAFE_ENV_TEMPLATES:
        return False
    return any(fnmatchcase(name, pattern) for pattern in SENSITIVE_FILE_PATTERNS)


def is_excluded_dir(directory: Path) -> bool:
    """Return True for dependency, build, cache and virtual-environment folders."""
    return directory.name.lower() in DEFAULT_EXCLUDED_DIRS or (directory / "pyvenv.cfg").is_file()


class GitIgnoreRules:
    """Applies every .gitignore in the tree, with deeper files overriding parent ones (like Git)."""

    def __init__(self) -> None:
        self._specs: dict[str, GitIgnoreSpec] = {}

    def load(self, root: Path, relative_dir: str) -> None:
        """Read the .gitignore in ``relative_dir`` (if any). Call once per visited directory."""
        gitignore = root / relative_dir / ".gitignore"
        if gitignore.is_file():
            lines = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
            self._specs[relative_dir] = GitIgnoreSpec.from_lines(lines)

    def is_ignored(self, relative_path: str, is_dir: bool) -> bool:
        """Check a POSIX-style repository-relative path against all applicable .gitignore files."""
        parts = relative_path.split("/")
        ignored = False
        for depth in range(len(parts)):
            base = "/".join(parts[:depth])
            spec = self._specs.get(base)
            if spec is None:
                continue
            sub_path = "/".join(parts[depth:]) + ("/" if is_dir else "")
            decision = spec.check_file(sub_path).include
            if decision is not None:
                ignored = decision
        return ignored
