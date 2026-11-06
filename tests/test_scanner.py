from pathlib import Path

import pytest

from app.services.repository.ignore_rules import is_sensitive_file
from app.services.repository.language import detect_language
from app.services.repository.scanner import RepositoryScanner
from tests.conftest import TEST_MAX_FILE_BYTES


@pytest.fixture
def scan_result(sample_repo: Path):
    return RepositoryScanner(max_file_bytes=TEST_MAX_FILE_BYTES).scan(sample_repo)


def test_scanner_indexes_only_safe_text_files(scan_result):
    assert sorted(f.path for f in scan_result.files) == [
        ".env.example",
        ".gitignore",
        "README.md",
        "app/main.py",
        "app/utils.py",
        "docs/.gitignore",
        "docs/keep.md",
        "package.json",
        "requirements.txt",
        "src/index.ts",
    ]


def test_scanner_reports_skip_reasons(scan_result):
    skipped = scan_result.skipped
    assert skipped["sensitive"] == 2  # .env, server.pem
    assert skipped["excluded_dir"] == 2  # node_modules, my_env (has pyvenv.cfg)
    assert skipped["gitignored"] == 3  # debug.log, build_output/, docs/drop.md
    assert skipped["binary"] == 1
    assert skipped["too_large"] == 1


def test_scanner_detects_languages_and_frameworks(scan_result):
    by_path = {f.path: f for f in scan_result.files}
    assert by_path["app/main.py"].language == "Python"
    assert by_path["app/main.py"].line_count == 4
    assert by_path["src/index.ts"].language == "TypeScript"
    assert scan_result.frameworks == ["FastAPI", "React"]
    assert scan_result.language_counts()["Python"] == 2


@pytest.mark.parametrize(
    ("name", "sensitive"),
    [
        (".env", True),
        (".env.production", True),
        ("prod.env", True),
        ("id_rsa", True),
        ("Server.PEM", True),
        ("credentials.json", True),
        (".env.example", False),
        ("environment.py", False),
        ("main.py", False),
    ],
)
def test_sensitive_file_detection(name: str, sensitive: bool):
    assert is_sensitive_file(name) is sensitive


@pytest.mark.parametrize(
    ("path", "language"),
    [("a/b.py", "Python"), ("Dockerfile", "Dockerfile"), ("x.PHP", "PHP"), ("notes.unknown", None)],
)
def test_detect_language(path: str, language: str | None):
    assert detect_language(path) == language
