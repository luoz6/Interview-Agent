import os
from pathlib import Path
import subprocess

import pytest

from app.services.principal_memory_ledger import (
    PrincipalMemoryLedgerError,
    ProtectedPrincipalMemoryLedger,
)


def _paths(tmp_path):
    workspace = (tmp_path / "workspace").resolve()
    protected = (tmp_path / "protected").resolve()
    workspace.mkdir()
    protected.mkdir()
    return workspace, protected


def _link_directory(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            raise
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail("Windows junction capability is required for H6")


def test_resolved_host_native_absolute_path_is_accepted(tmp_path):
    workspace, protected = _paths(tmp_path)
    ledger = ProtectedPrincipalMemoryLedger(
        (protected / "ledger.jsonl").resolve(),
        workspace=workspace,
    )
    assert ledger.resolved_path == (protected / "ledger.jsonl").resolve()


def test_relative_path_and_relative_workspace_are_rejected(tmp_path):
    workspace, protected = _paths(tmp_path)
    with pytest.raises(PrincipalMemoryLedgerError) as relative:
        ProtectedPrincipalMemoryLedger(Path("ledger.jsonl"), workspace=workspace)
    assert relative.value.gate_code == "TOMBSTONE_LEDGER_PATH_INVALID"

    with pytest.raises(PrincipalMemoryLedgerError) as relative_workspace:
        ProtectedPrincipalMemoryLedger(
            protected / "ledger.jsonl",
            workspace=Path("workspace"),
        )
    assert relative_workspace.value.gate_code == "TOMBSTONE_LEDGER_PATH_INVALID"


def test_workspace_containment_and_symlink_escape_are_rejected(tmp_path):
    workspace, protected = _paths(tmp_path)
    with pytest.raises(PrincipalMemoryLedgerError) as contained:
        ProtectedPrincipalMemoryLedger(
            workspace / "ledger.jsonl",
            workspace=workspace,
        )
    assert contained.value.gate_code == "TOMBSTONE_LEDGER_PATH_INVALID"

    link = workspace / "escape"
    _link_directory(link, protected)
    with pytest.raises(PrincipalMemoryLedgerError) as escaped:
        ProtectedPrincipalMemoryLedger(
            link / "ledger.jsonl",
            workspace=workspace,
        )
    assert escaped.value.gate_code == "TOMBSTONE_LEDGER_PATH_INVALID"


def test_external_symlink_or_junction_path_is_rejected(tmp_path):
    workspace, protected = _paths(tmp_path)
    actual = tmp_path / "actual"
    actual.mkdir()
    link = protected / "linked"
    _link_directory(link, actual)
    with pytest.raises(PrincipalMemoryLedgerError) as captured:
        ProtectedPrincipalMemoryLedger(
            link / "ledger.jsonl",
            workspace=workspace,
        )
    assert captured.value.gate_code == "TOMBSTONE_LEDGER_PATH_INVALID"


@pytest.mark.skipif(os.name != "nt", reason="Windows path contract")
def test_windows_drive_path_is_accepted_and_posix_root_is_rejected(tmp_path):
    workspace, protected = _paths(tmp_path)
    assert (protected / "ledger.jsonl").drive
    ProtectedPrincipalMemoryLedger(
        protected / "ledger.jsonl",
        workspace=workspace,
    )
    with pytest.raises(PrincipalMemoryLedgerError) as posix:
        ProtectedPrincipalMemoryLedger(
            Path("/var/lib/interview/ledger.jsonl"),
            workspace=workspace,
        )
    assert posix.value.gate_code == "TOMBSTONE_LEDGER_PATH_INVALID"


@pytest.mark.skipif(os.name != "nt", reason="Windows UNC contract")
def test_windows_unc_path_is_rejected(tmp_path):
    workspace, _ = _paths(tmp_path)
    with pytest.raises(PrincipalMemoryLedgerError) as unc:
        ProtectedPrincipalMemoryLedger(
            Path(r"\\server\protected\ledger.jsonl"),
            workspace=workspace,
        )
    assert unc.value.gate_code == "TOMBSTONE_LEDGER_PATH_INVALID"


@pytest.mark.skipif(os.name == "nt", reason="POSIX path contract")
def test_linux_posix_root_is_accepted_and_windows_literal_is_rejected(tmp_path):
    workspace, protected = _paths(tmp_path)
    assert str(protected).startswith("/")
    ProtectedPrincipalMemoryLedger(
        protected / "ledger.jsonl",
        workspace=workspace,
    )
    with pytest.raises(PrincipalMemoryLedgerError) as windows:
        ProtectedPrincipalMemoryLedger(
            Path(r"C:\protected\ledger.jsonl"),
            workspace=workspace,
        )
    assert windows.value.gate_code == "TOMBSTONE_LEDGER_PATH_INVALID"
