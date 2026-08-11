"""Cross-platform reproducibility and dependency binding contracts."""

import json
import hashlib
import os
from pathlib import Path

import pytest

from scripts.compile_requirements_lock import (
    LOCK_COMMAND_TEMPLATE,
    PIP_TOOLS_VERSION,
    PIP_VERSION,
)
from scripts.reproducibility_preflight import (
    NODE_VERSION_UNSUPPORTED,
    PYTHON_ENVIRONMENT_MISMATCH,
    PYTHON_VERSION_UNSUPPORTED,
    ReproducibilityPreflightError,
    dependency_inventory_sha256,
    validate_node_version,
    validate_python_environment,
)


def _canonical_text_sha256(path):
    canonical = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_python_311_environment_is_required_before_execution(tmp_path):
    prefix = tmp_path / "venv"
    executable = prefix / ("Scripts/python.exe" if __import__("os").name == "nt" else "bin/python")

    result = validate_python_environment(
        version_info=(3, 11, 9),
        executable=executable,
        prefix=prefix,
        base_prefix=tmp_path / "base",
        virtual_env=str(prefix),
        require_venv=True,
    )

    assert result == {
        "python_major_minor": "3.11",
        "python_executable_source": "virtualenv",
    }


def test_unsupported_python_fails_with_stable_gate(tmp_path):
    with pytest.raises(ReproducibilityPreflightError) as captured:
        validate_python_environment(
            version_info=(3, 8, 20),
            executable=tmp_path / "python",
            prefix=tmp_path,
            base_prefix=tmp_path,
        )
    assert captured.value.gate_code == PYTHON_VERSION_UNSUPPORTED


@pytest.mark.parametrize("require_venv", [False, True])
def test_virtual_environment_mismatch_fails_closed(tmp_path, require_venv):
    with pytest.raises(ReproducibilityPreflightError) as captured:
        validate_python_environment(
            version_info=(3, 11, 9),
            executable=tmp_path / "wrong" / "python",
            prefix=tmp_path / "active",
            base_prefix=tmp_path / "base",
            virtual_env=str(tmp_path / "declared"),
            require_venv=require_venv,
        )
    assert captured.value.gate_code == PYTHON_ENVIRONMENT_MISMATCH


def test_executable_outside_prefix_fails_without_virtual_env_variable(tmp_path):
    with pytest.raises(ReproducibilityPreflightError) as captured:
        validate_python_environment(
            version_info=(3, 11, 9),
            executable=tmp_path / "wrong" / "python",
            prefix=tmp_path / "active",
            base_prefix=tmp_path / "base",
            virtual_env=None,
        )
    assert captured.value.gate_code == PYTHON_ENVIRONMENT_MISMATCH


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv interpreter symlink")
def test_venv_interpreter_symlink_to_base_python_is_accepted(tmp_path):
    base = tmp_path / "base"
    base_bin = base / "bin"
    base_bin.mkdir(parents=True)
    base_python = base_bin / "python3.11"
    base_python.touch()
    prefix = tmp_path / "venv"
    prefix_bin = prefix / "bin"
    prefix_bin.mkdir(parents=True)
    executable = prefix_bin / "python"
    executable.symlink_to(base_python)

    result = validate_python_environment(
        version_info=(3, 11, 9),
        executable=executable,
        prefix=prefix,
        base_prefix=base,
        virtual_env=None,
        require_venv=True,
    )

    assert result["python_executable_source"] == "virtualenv"


def test_primary_node_matrix_requires_node_22():
    assert validate_node_version("v22.21.0") == "22.21.0"
    with pytest.raises(ReproducibilityPreflightError) as captured:
        validate_node_version("v20.18.0")
    assert captured.value.gate_code == NODE_VERSION_UNSUPPORTED
    assert validate_node_version("v20.18.0", allow_node20=True) == "20.18.0"


def test_dependency_inventory_digest_is_order_independent_and_content_sensitive():
    class Distribution:
        def __init__(self, name, version):
            self.metadata = {"Name": name}
            self.version = version

    left = [Distribution("Example_B", "2"), Distribution("example-a", "1")]
    right = list(reversed(left))
    changed = [Distribution("Example_B", "3"), Distribution("example-a", "1")]

    assert dependency_inventory_sha256(left) == dependency_inventory_sha256(right)
    assert dependency_inventory_sha256(left) != dependency_inventory_sha256(changed)
    assert dependency_inventory_sha256([]) == hashlib.sha256(b"[]").hexdigest()


def test_dependency_source_generator_and_lock_metadata_are_bound():
    root = Path(__file__).resolve().parents[2]
    source = (root / "requirements.txt").read_text(encoding="utf-8")
    tooling = (root / "requirements-tooling.txt").read_text(encoding="utf-8")
    windows_lock = (root / "requirements-windows.lock.txt").read_text(
        encoding="utf-8"
    )
    linux_lock = (root / "requirements-linux.lock.txt").read_text(
        encoding="utf-8"
    )
    legacy_lock = (root / "requirements.lock.txt").read_bytes()
    metadata = json.loads(
        (root / "requirements.lock.meta.json").read_text(encoding="utf-8")
    )

    assert 'colorama>=0.4.6; sys_platform == "win32"' in source
    assert (
        'uvloop>=0.15.1; sys_platform != "win32" and '
        'platform_python_implementation != "PyPy"'
    ) in source
    assert tooling.splitlines()[-2:] == ["pip==25.1.1", "pip-tools==7.6.0"]
    assert PIP_TOOLS_VERSION == "7.6.0"
    assert PIP_VERSION == "25.1.1"
    assert metadata["command_template"] == LOCK_COMMAND_TEMPLATE
    assert "uvloop==" in linux_lock and "sys_platform != \"win32\"" in linux_lock
    assert "colorama==" not in linux_lock
    assert "colorama==" in windows_lock
    assert "uvloop==" not in windows_lock
    assert legacy_lock == (root / "requirements-windows.lock.txt").read_bytes()
    assert metadata["source_digest_mode"] == "canonical_utf8_lf"
    assert metadata["tooling_digest_mode"] == "canonical_utf8_lf"
    assert _canonical_text_sha256(root / metadata["source_file"]) == metadata[
        "source_sha256"
    ]
    assert _canonical_text_sha256(root / metadata["tooling_file"]) == metadata[
        "tooling_sha256"
    ]
    for lock in metadata["locks"].values():
        assert lock["digest_mode"] == "canonical_utf8_lf"
        assert _canonical_text_sha256(root / lock["file"]) == lock["sha256"]
    alias = metadata["legacy_windows_alias"]
    assert alias["digest_mode"] == "canonical_utf8_lf"
    assert _canonical_text_sha256(root / alias["file"]) == alias["sha256"]
