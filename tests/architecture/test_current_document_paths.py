from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_DIRECTORIES = (
    ROOT / "docs" / "superpowers" / "plans",
    ROOT / "docs" / "superpowers" / "specs",
)
EXCLUDED_CURRENT_DOCUMENTS = frozenset(
    {
        ROOT / "docs" / "hosted-v2-control-foundation-readiness-audit.md",
        ROOT / "docs" / "local-v1-long-term-memory-execution-baseline.md",
        ROOT / "docs" / "long-term-memory-production-execution-baseline.md",
        ROOT / "docs" / "refactoring-plan.md",
    }
)
SCRIPT_MODULE_PATTERN = re.compile(
    r"(?<![\w.-])python(?:\.exe)?\s+-m\s+"
    r"(?P<module>scripts(?:\.[A-Za-z_][A-Za-z0-9_]*)+)"
)
TEST_PATH_PATTERN = re.compile(
    r"(?P<path>tests[\\/][A-Za-z0-9_.\\/-]+\.(?:py|js))(?![A-Za-z0-9_])"
)


def _is_below(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _current_documents() -> tuple[Path, ...]:
    candidates = [ROOT / "README.md", *(ROOT / "docs").rglob("*.md")]
    return tuple(
        sorted(
            path
            for path in candidates
            if path not in EXCLUDED_CURRENT_DOCUMENTS
            and not any(_is_below(path, archive) for archive in ARCHIVE_DIRECTORIES)
        )
    )


def _script_modules(text: str) -> set[str]:
    return {match.group("module") for match in SCRIPT_MODULE_PATTERN.finditer(text)}


def _test_paths(text: str) -> set[str]:
    return {
        match.group("path").replace("\\", "/")
        for match in TEST_PATH_PATTERN.finditer(text)
    }


def _module_exists(module: str) -> bool:
    module_path = ROOT.joinpath(*module.split("."))
    return module_path.with_suffix(".py").is_file() or (
        module_path / "__init__.py"
    ).is_file()


def test_reference_parser_recognizes_supported_current_paths():
    text = (
        "Run `python -m scripts.release_artifact_audit --profile stage40`, "
        "then execute `tests/architecture/test_repository_acceptance.py` and "
        "`tests\\contracts\\test_utf8_text_contract.py`; ignore "
        "`tests/golden/report_quality_v1.json`."
    )

    assert _script_modules(text) == {"scripts.release_artifact_audit"}
    assert _test_paths(text) == {
        "tests/architecture/test_repository_acceptance.py",
        "tests/contracts/test_utf8_text_contract.py",
    }


def test_historical_plan_and_spec_directories_have_explicit_boundaries():
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for archive in ARCHIVE_DIRECTORIES:
        assert (archive / "README.md").is_file()
        relative = archive.relative_to(ROOT).as_posix()
        assert relative in root_readme


def test_current_document_script_modules_and_test_paths_exist():
    findings: list[str] = []

    for document in _current_documents():
        text = document.read_text(encoding="utf-8-sig")
        relative_document = document.relative_to(ROOT).as_posix()

        for module in sorted(_script_modules(text)):
            if not _module_exists(module):
                findings.append(f"{relative_document}: missing module {module}")

        for referenced_path in sorted(_test_paths(text)):
            if not (ROOT / referenced_path).is_file():
                findings.append(
                    f"{relative_document}: missing test path {referenced_path}"
                )

    assert findings == []
