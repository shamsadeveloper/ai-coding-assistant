from pathlib import Path

import pytest

from app.services.repository.errors import InvalidRepositoryPathError, PathOutsideRepositoryError
from app.services.repository.path_guard import resolve_inside, resolve_repository_root


@pytest.mark.parametrize(
    "bad_path",
    [
        "../outside.txt",
        "app/../../outside.txt",
        "..\\outside.txt",
        "/etc/passwd",
        "\\Windows\\win.ini",
        "C:/Windows/win.ini",
        "C:relative.txt",
        "//server/share/file.txt",
        "app/main.py:hidden_stream",
        "",
        "app/\x00main.py",
    ],
)
def test_resolve_inside_rejects_escapes(sample_repo: Path, bad_path: str):
    with pytest.raises(PathOutsideRepositoryError):
        resolve_inside(sample_repo, bad_path)


@pytest.mark.parametrize("good_path", ["app/main.py", "app\\main.py", "./app/main.py", "app/./main.py"])
def test_resolve_inside_accepts_relative_paths(sample_repo: Path, good_path: str):
    assert resolve_inside(sample_repo, good_path) == sample_repo / "app" / "main.py"


def test_resolve_inside_rejects_symlink_pointing_outside(sample_repo: Path, tmp_path: Path):
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = sample_repo / "app" / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks needs Developer Mode or admin rights on Windows")

    with pytest.raises(PathOutsideRepositoryError):
        resolve_inside(sample_repo, "app/link.txt")


def test_register_root_requires_absolute_existing_folder(sample_repo: Path):
    assert resolve_repository_root(str(sample_repo), []) == sample_repo

    with pytest.raises(InvalidRepositoryPathError):
        resolve_repository_root("relative/folder", [])
    with pytest.raises(InvalidRepositoryPathError):
        resolve_repository_root(str(sample_repo / "missing"), [])
    with pytest.raises(InvalidRepositoryPathError):
        resolve_repository_root(str(sample_repo / "app" / "main.py"), [])


def test_register_root_rejects_drive_root():
    with pytest.raises(InvalidRepositoryPathError):
        resolve_repository_root(Path.cwd().anchor, [])


def test_register_root_enforces_allowed_roots(sample_repo: Path, tmp_path: Path):
    assert resolve_repository_root(str(sample_repo), [str(tmp_path)]) == sample_repo

    with pytest.raises(InvalidRepositoryPathError):
        resolve_repository_root(str(sample_repo), [str(sample_repo / "app")])
