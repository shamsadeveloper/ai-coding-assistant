"""A throwaway copy of the repository where candidate patches are applied and tested.

The original repository is never written by a fix session. The copy leaves out everything tests
must not see or need: .git, virtualenvs and dependency folders, .gitignore'd files, secret files
(.env, keys, credentials), symlinks/junctions, and the assistant's own data folder.

Tests in the sandbox run with:
- the working directory inside the copy, and PYTHONPATH pointing at the copy (so an editable
  install of the original package cannot shadow the patched code);
- HOME/USERPROFILE/APPDATA/TEMP redirected to an empty folder (no ~/.aws, ~/.pypirc, ...);
- the minimal environment allowlist (no API keys or other secrets from the server).
This is process-level isolation, not an OS sandbox: see docs/SECURITY.md.
"""

import os
import shutil
import stat
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from app.services.fixing.errors import SandboxError
from app.services.repository.ignore_rules import GitIgnoreRules, is_excluded_dir, is_sensitive_file
from app.services.repository.path_guard import resolve_inside
from app.services.repository.scanner import _is_link
from app.services.testing.runner import minimal_env

_REDIRECTED_HOME_VARS = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA")


def remove_tree(path: Path) -> None:
    """Delete a folder, including read-only files (common on Windows, e.g. copied .pyc files)."""

    def make_writable_and_retry(function, target, _exc) -> None:
        os.chmod(target, stat.S_IWRITE)
        function(target)

    if path.exists():
        shutil.rmtree(path, onexc=make_writable_and_retry)


@dataclass
class Sandbox:
    base: Path  # data/sandboxes/<name>
    root: Path  # the repository copy
    home: Path  # empty home/temp folder for the test process
    files: int = 0
    bytes: int = 0
    skipped: Counter[str] = field(default_factory=Counter)

    @classmethod
    def create(
        cls,
        source: Path,
        base: Path,
        *,
        max_files: int,
        max_bytes: int,
        exclude: Iterable[Path] = (),
    ) -> "Sandbox":
        remove_tree(base)
        sandbox = cls(base=base, root=base / "repo", home=base / "home")
        sandbox.root.mkdir(parents=True)
        (sandbox.home / "tmp").mkdir(parents=True)
        excluded = {p.resolve() for p in exclude}
        try:
            sandbox._copy(source.resolve(), excluded, max_files, max_bytes)
        except BaseException:
            remove_tree(base)
            raise
        return sandbox

    def _copy(self, source: Path, excluded: set[Path], max_files: int, max_bytes: int) -> None:
        rules = GitIgnoreRules()
        for dir_path, dir_names, file_names in os.walk(source):
            current = Path(dir_path)
            relative_dir = "" if current == source else current.relative_to(source).as_posix()
            rules.load(source, relative_dir)

            kept = []
            for name in dir_names:
                directory = current / name
                relative = f"{relative_dir}/{name}" if relative_dir else name
                if _is_link(directory):
                    self.skipped["link"] += 1
                elif directory.resolve() in excluded:
                    self.skipped["assistant_data"] += 1
                elif is_excluded_dir(directory):
                    self.skipped["excluded_dir"] += 1
                elif rules.is_ignored(relative, is_dir=True):
                    self.skipped["gitignored"] += 1
                else:
                    kept.append(name)
            dir_names[:] = kept

            for name in file_names:
                path = current / name
                relative = f"{relative_dir}/{name}" if relative_dir else name
                if is_sensitive_file(name):
                    self.skipped["sensitive"] += 1
                    continue
                if _is_link(path):
                    self.skipped["link"] += 1
                    continue
                if rules.is_ignored(relative, is_dir=False):
                    self.skipped["gitignored"] += 1
                    continue
                size = path.stat().st_size
                self.files += 1
                self.bytes += size
                if self.files > max_files or self.bytes > max_bytes:
                    raise SandboxError(
                        f"The repository is too large for a sandbox copy (limit {max_files} files / "
                        f"{max_bytes // 1_000_000} MB); raise APP_FIX_SANDBOX_MAX_FILES or APP_FIX_SANDBOX_MAX_BYTES"
                    )
                target = self.root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)

    def write(self, relative_path: str, content: str) -> None:
        """Write a candidate file into the copy (never into the original repository)."""
        target = resolve_inside(self.root, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)

    def delete(self, relative_path: str) -> None:
        target = resolve_inside(self.root, relative_path)
        if target.is_file():
            target.unlink()

    def env(self) -> dict[str, str]:
        env = minimal_env()
        for key in _REDIRECTED_HOME_VARS:
            env[key] = str(self.home)
        env["TEMP"] = env["TMP"] = str(self.home / "tmp")
        python_path = [str(self.root)] + ([str(self.root / "src")] if (self.root / "src").is_dir() else [])
        env["PYTHONPATH"] = os.pathsep.join(python_path)
        env["PYTHONNOUSERSITE"] = "1"
        return env

    def remove(self) -> None:
        remove_tree(self.base)
