from __future__ import annotations

from copy import deepcopy
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.verify_interview_quality_v1_publication import (
    INDEX_SCHEMA,
    MANIFEST_SCHEMA,
    REQUIRED_EVIDENCE_KINDS,
    TrustedGitRunner,
    _changed_paths,
    _contains_machine_specific_path,
    _git_regular_file_blob,
    PublicationVerificationError,
    _publication_path_allowed,
    _local_git_config_safe,
    _resolve_commit,
    _resolve_git_metadata,
    canonical_bytes,
    evidence_bundle_sha256,
    file_identity,
    main,
    public_key_fingerprint,
    safe_repo_file,
    validate_evidence_index,
    verify_publication,
)


MANIFEST_PATH = "docs/interview-quality-v1-execution-manifest.json"
INDEX_PATH = "docs/interview-quality-v1-evidence-index.json"
PUBLICATION_REF = "refs/tags/interview-quality-v1-publication-v1"
FINAL_REF = "refs/tags/interview-quality-v1-final-v1"
EVIDENCE_DIR = "docs/interview-quality-v1-publication-evidence"
EVIDENCE_PATH = f"{EVIDENCE_DIR}/t65-formal-provider.json"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _native_git_executable() -> Path:
    discovered = shutil.which("git")
    if discovered is None:
        pytest.skip("Git is unavailable")
    command = Path(discovered).resolve()
    candidates = [command]
    if command.parent.name.casefold() in {"cmd", "bin"}:
        candidates.insert(0, command.parent.parent / "mingw64" / "bin" / "git.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    pytest.skip("native Git executable is unavailable")


def _write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8", newline="")


def _write_json(path: Path, value: object) -> None:
    _write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _signed_block(private_key: Ed25519PrivateKey, payload: dict[str, object]) -> dict[str, object]:
    raw_public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "signed_payload": payload,
        "signer_fingerprint": public_key_fingerprint(raw_public_key),
        "signature_base64": base64.b64encode(
            private_key.sign(canonical_bytes(payload))
        ).decode("ascii"),
    }


def _commit(root: Path, message: str) -> tuple[str, str]:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD"), _git(root, "show", "-s", "--format=%T", "HEAD")


def _index_entry(
    root: Path,
    *,
    implementation_sha: str,
    implementation_tree: str,
    evidence_id: str,
    kind: str,
    relative: str,
) -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "task": kind.split("_", 1)[0],
        "gate": "Gate 7",
        "kind": kind,
        "path": relative,
        **file_identity(root / relative),
        "sensitivity": "PUBLIC_SANITIZED",
        "publishable": True,
        "contains_secrets": False,
        "contains_real_candidate_data": False,
        "redaction_status": "PASS",
        "status": "PASS",
        "bound_revision": implementation_sha,
        "bound_tree": implementation_tree,
    }


def _index(
    entries: list[dict[str, object]],
    *,
    implementation_sha: str,
    implementation_tree: str,
) -> dict[str, object]:
    return {
        "schema_version": INDEX_SCHEMA,
        "hash_algorithm": "sha256-raw-file-bytes-v1",
        "implementation_sha": implementation_sha,
        "implementation_tree": implementation_tree,
        "entries": entries,
        "entry_count": len(entries),
        "bundle_sha256": evidence_bundle_sha256(entries),
    }


@pytest.fixture
def publication_repo(tmp_path: Path):
    root = tmp_path / "publication"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "quality-verifier@example.invalid")
    _git(root, "config", "user.name", "Quality Verifier")
    _git(root, "config", "core.autocrlf", "false")
    signing_key = Ed25519PrivateKey.generate()
    raw_public_key = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    signer_fingerprint = public_key_fingerprint(raw_public_key)
    native_git = _native_git_executable()
    native_git_sha256 = file_identity(native_git)["sha256"]
    git_runner = TrustedGitRunner(native_git, str(native_git_sha256))

    _write(root / "app/main.py", "VALUE = 1\n")
    implementation_sha, implementation_tree = _commit(root, "implementation")

    evidence_specs = [
        ("t65-formal-provider-v1", "T65_FORMAL_PROVIDER", "t65-formal-provider.json"),
        ("t68-documentation-v1", "T68_DOCUMENTATION", "t68-documentation.json"),
        ("t69-manifest-v1", "T69_MANIFEST_COMPLETENESS", "t69-manifest.json"),
        ("t70-human-review-v1", "T70_INDEPENDENT_HUMAN_REVIEW", "t70-human-review.json"),
        ("t71-revision-freeze-v1", "T71_REVISION_FREEZE", "t71-revision-freeze.json"),
        ("t72-final-acceptance-v1", "T72_FINAL_ACCEPTANCE", "t72-final-acceptance.json"),
    ]
    for evidence_id, kind, filename in evidence_specs:
        _write(
            root / EVIDENCE_DIR / filename,
            json.dumps(
                {
                    "evidence_id": evidence_id,
                    "kind": kind,
                    "status": "PASS",
                    "bound_revision": implementation_sha,
                    "bound_tree": implementation_tree,
                }
            )
            + "\n",
        )
    index = _index(
        [
            _index_entry(
                root,
                implementation_sha=implementation_sha,
                implementation_tree=implementation_tree,
                evidence_id=evidence_id,
                kind=kind,
                relative=f"{EVIDENCE_DIR}/{filename}",
            )
            for evidence_id, kind, filename in evidence_specs
        ],
        implementation_sha=implementation_sha,
        implementation_tree=implementation_tree,
    )
    _write_json(root / INDEX_PATH, index)
    publication_sha, publication_tree = _commit(root, "publication payload")
    _git(root, "tag", PUBLICATION_REF.removeprefix("refs/tags/"), publication_sha)

    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "publication_commit_self_hash_recorded": False,
        "revisions": {
            "implementation": {
                "sha": implementation_sha,
                "tree": implementation_tree,
            },
            "publication": {
                "sha": publication_sha,
                "tree": publication_tree,
                "ref": PUBLICATION_REF,
            },
            "final": {
                "ref": FINAL_REF,
                "sha": None,
                "tree": None,
                "self_sha_recorded": False,
            },
        },
        "acceptance": {
            "engineering_status": "PASS",
            "quality_status": "PASS",
            "final_acceptance": "PASS",
            "execution_authorized": True,
            "t65_formal_provider_status": "PASS",
            "independent_human_review": {
                "status": "PASS",
                "reviewer_independent": True,
                "evidence_id": "t70-human-review-v1",
                **_signed_block(
                    signing_key,
                    {
                        "schema_version": "interview-quality-v1-independent-review-signature-v1",
                        "implementation_sha": implementation_sha,
                        "implementation_tree": implementation_tree,
                        "evidence_id": "t70-human-review-v1",
                        "reviewer_independent": True,
                        "status": "PASS",
                    },
                ),
            },
            "required_tests": {"status": "PASS", "total": 38, "failed": 0},
            "blocking_skips": 0,
            "unresolved_findings": {"p0": 0, "p1": 0},
        },
        "metrics": {
            "provider_calls": {
                "status": "PASS",
                "value": 12,
                "unit": "requests",
            }
        },
        "evidence_index": {
            "path": INDEX_PATH,
            **file_identity(root / INDEX_PATH),
        },
    }
    _write_json(root / MANIFEST_PATH, manifest)
    final_sha, final_tree = _commit(root, "final closure")
    _git(root, "tag", FINAL_REF.removeprefix("refs/tags/"), final_sha)
    final_ref_receipt = _signed_block(
        signing_key,
        {
            "schema_version": "interview-quality-v1-final-ref-receipt-v1",
            "implementation_sha": implementation_sha,
            "implementation_tree": implementation_tree,
            "publication_sha": publication_sha,
            "publication_tree": publication_tree,
            "final_ref": FINAL_REF,
            "final_sha": final_sha,
            "final_tree": final_tree,
            "status": "PASS",
        },
    )
    receipt_path = tmp_path / "final-ref-receipt.json"
    _write_json(receipt_path, final_ref_receipt)
    return {
        "root": root,
        "manifest": manifest,
        "index": index,
        "implementation_sha": implementation_sha,
        "implementation_tree": implementation_tree,
        "publication_sha": publication_sha,
        "publication_tree": publication_tree,
        "final_sha": final_sha,
        "final_tree": final_tree,
        "final_ref_receipt": final_ref_receipt,
        "receipt_path": receipt_path,
        "trusted_public_keys": {signer_fingerprint: raw_public_key},
        "git_runner": git_runner,
        "native_git": native_git,
        "native_git_sha256": native_git_sha256,
    }


def _verify(bundle, *, manifest=None, index=None):
    return verify_publication(
        manifest or bundle["manifest"],
        index or bundle["index"],
        root=bundle["root"],
        manifest_path=MANIFEST_PATH,
        index_path=INDEX_PATH,
        trusted_public_keys=bundle["trusted_public_keys"],
        final_ref_receipt=bundle["final_ref_receipt"],
        git_runner=bundle["git_runner"],
    )


def _assert_code(exc: pytest.ExceptionInfo[PublicationVerificationError], code: str) -> None:
    assert code in exc.value.codes


def _write_indexed_evidence_note(publication_repo, content: str) -> dict[str, object]:
    root = publication_repo["root"]
    document = json.loads((root / EVIDENCE_PATH).read_text(encoding="utf-8"))
    document["publication_note"] = content
    _write_json(root / EVIDENCE_PATH, document)
    index = deepcopy(publication_repo["index"])
    index["entries"][0].update(file_identity(root / EVIDENCE_PATH))
    index["bundle_sha256"] = evidence_bundle_sha256(index["entries"])
    return index


def test_valid_i_to_p_to_f_bundle_passes_without_self_hash(publication_repo):
    result = _verify(publication_repo)

    assert result == {
        "status": "PASS",
        "implementation_sha": publication_repo["implementation_sha"],
        "publication_sha": publication_repo["publication_sha"],
        "final_sha": publication_repo["final_sha"],
        "entries_verified": len(REQUIRED_EVIDENCE_KINDS),
    }
    assert publication_repo["manifest"]["revisions"]["final"]["sha"] is None
    assert publication_repo["manifest"]["revisions"]["final"]["tree"] is None


def test_cli_passes_for_frozen_bundle(publication_repo, capsys, monkeypatch):
    monkeypatch.setattr(
        "scripts.verify_interview_quality_v1_publication.TRUSTED_PUBLICATION_AUTHORITY_PUBLIC_KEYS",
        publication_repo["trusted_public_keys"],
    )
    monkeypatch.setattr(
        "scripts.verify_interview_quality_v1_publication._NATIVE_GIT_CANDIDATES",
        (publication_repo["native_git"],),
    )
    normalized_git = str(publication_repo["native_git"]).replace("\\", "/").casefold()
    monkeypatch.setattr(
        "scripts.verify_interview_quality_v1_publication.TRUSTED_GIT_EXECUTABLE_SHA256_BY_PATH",
        {normalized_git: frozenset({publication_repo["native_git_sha256"]})},
    )
    assert main(
        [
            "--root",
            str(publication_repo["root"]),
            "--manifest",
            MANIFEST_PATH,
            "--index",
            INDEX_PATH,
            "--final-ref-receipt",
            str(publication_repo["receipt_path"]),
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "INTERVIEW_QUALITY_V1_PUBLICATION=PASS" in output
    assert publication_repo["final_sha"] in output


def test_cli_defaults_to_blocked_without_deployed_trust_root(
    publication_repo, capsys, monkeypatch
):
    monkeypatch.setattr(
        "scripts.verify_interview_quality_v1_publication.TRUSTED_GIT_EXECUTABLE_SHA256_BY_PATH",
        {},
    )
    monkeypatch.setattr(
        "scripts.verify_interview_quality_v1_publication.subprocess.run",
        lambda *args, **kwargs: pytest.fail("Git subprocess must not run without trust"),
    )
    assert main(
        [
            "--root",
            str(publication_repo["root"]),
            "--manifest",
            MANIFEST_PATH,
            "--index",
            INDEX_PATH,
            "--final-ref-receipt",
            str(publication_repo["receipt_path"]),
        ]
    ) == 1
    output = capsys.readouterr().out
    assert "INTERVIEW_QUALITY_V1_PUBLICATION=BLOCKED" in output
    assert "GATE=TRUSTED_GIT_UNAVAILABLE" in output


def test_injected_runner_uses_absolute_pinned_git_and_sanitized_environment(
    publication_repo, monkeypatch, tmp_path
):
    malicious = tmp_path / "git.exe"
    malicious.write_bytes(b"not a real executable")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "malicious.gitconfig"))
    monkeypatch.setenv("GIT_REPLACE_REF_BASE", "refs/evil/")
    real_run = subprocess.run
    observed: list[tuple[list[str], dict[str, str]]] = []

    def recording_run(command, **kwargs):
        observed.append((list(command), dict(kwargs["env"])))
        return real_run(command, **kwargs)

    monkeypatch.setattr(
        "scripts.verify_interview_quality_v1_publication.subprocess.run",
        recording_run,
    )
    assert _verify(publication_repo)["status"] == "PASS"
    assert observed
    for command, environment in observed:
        assert Path(command[0]) == publication_repo["native_git"]
        assert "--no-replace-objects" in command
        assert "--no-optional-locks" in command
        assert f"core.excludesFile={os.devnull}" in command
        assert f"core.attributesFile={os.devnull}" in command
        assert "PATH" not in environment
        assert "GIT_REPLACE_REF_BASE" not in environment
        assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
        assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
        assert environment["LANG"] == "C"
        assert environment["LC_ALL"] == "C"


def test_trusted_git_executable_drift_blocks_before_execution(publication_repo, tmp_path):
    copied_git = tmp_path / publication_repo["native_git"].name
    shutil.copy2(publication_repo["native_git"], copied_git)
    runner = TrustedGitRunner(copied_git, file_identity(copied_git)["sha256"])
    copied_git.write_bytes(copied_git.read_bytes() + b"drift")
    with pytest.raises(PublicationVerificationError) as raised:
        runner.run(publication_repo["root"], ("status", "--porcelain"))
    _assert_code(raised, "TRUSTED_GIT_EXECUTABLE_DRIFT")


def test_trusted_git_stat_identity_drift_blocks_even_when_bytes_match(
    publication_repo, tmp_path
):
    copied_git = tmp_path / publication_repo["native_git"].name
    shutil.copy2(publication_repo["native_git"], copied_git)
    runner = TrustedGitRunner(copied_git, file_identity(copied_git)["sha256"])
    before = copied_git.stat().st_mtime_ns
    os.utime(copied_git, ns=(before + 1_000_000_000, before + 1_000_000_000))
    with pytest.raises(PublicationVerificationError) as raised:
        runner.run(publication_repo["root"], ("status", "--porcelain"))
    _assert_code(raised, "TRUSTED_GIT_EXECUTABLE_DRIFT")


def test_trusted_git_rechecks_identity_after_failed_process(
    publication_repo, tmp_path, monkeypatch
):
    copied_git = tmp_path / publication_repo["native_git"].name
    shutil.copy2(publication_repo["native_git"], copied_git)
    runner = TrustedGitRunner(copied_git, file_identity(copied_git)["sha256"])

    def mutate_and_fail(*args, **kwargs):
        copied_git.write_bytes(copied_git.read_bytes() + b"drift")
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(
        "scripts.verify_interview_quality_v1_publication.subprocess.run",
        mutate_and_fail,
    )
    with pytest.raises(PublicationVerificationError) as raised:
        runner.run(publication_repo["root"], ("status", "--porcelain"))
    _assert_code(raised, "TRUSTED_GIT_EXECUTABLE_DRIFT")


def test_trusted_git_timeout_without_identity_drift_is_fail_closed(
    publication_repo, monkeypatch
):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(
        "scripts.verify_interview_quality_v1_publication.subprocess.run", timeout
    )
    with pytest.raises(PublicationVerificationError) as raised:
        publication_repo["git_runner"].run(
            publication_repo["root"], ("status", "--porcelain")
        )
    _assert_code(raised, "GIT_COMMAND_FAILED")


def test_trusted_git_rejects_non_native_file_even_with_matching_hash(tmp_path):
    fake_git = tmp_path / "git.exe"
    fake_git.write_text("not a native executable", encoding="utf-8")
    with pytest.raises(ValueError, match="native executable"):
        TrustedGitRunner(fake_git, file_identity(fake_git)["sha256"])


def test_git_metadata_resolver_supports_bounded_linked_worktree(tmp_path):
    root = tmp_path / "worktree"
    root.mkdir()
    common = tmp_path / "common" / ".git"
    git_directory = common / "worktrees" / "worktree"
    git_directory.mkdir(parents=True)
    (common / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n", encoding="utf-8"
    )
    (git_directory / "commondir").write_text("../..\n", encoding="utf-8")
    (root / ".git").write_text(
        f"gitdir: {git_directory.as_posix()}\n", encoding="utf-8"
    )
    assert _resolve_git_metadata(root) == (
        git_directory.resolve(),
        common.resolve(),
    )
    assert _local_git_config_safe(root)


@pytest.mark.parametrize(
    "relative",
    ["commondir", "config.worktree", "objects/info/alternates"],
)
def test_git_metadata_rejects_unsafe_indirection_before_execution(tmp_path, relative):
    root = tmp_path / "repo"
    metadata = root / ".git"
    (metadata / "objects" / "info").mkdir(parents=True)
    (metadata / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n", encoding="utf-8"
    )
    target = metadata / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("external\n", encoding="utf-8")
    assert _resolve_git_metadata(root) is None


@pytest.mark.parametrize(
    "config",
    [
        "[include]\n\tpath = malicious.config\n",
        '[includeIf "gitdir:C:/evil"]\n\tpath = malicious.config\n',
        '[filter "evil"]\n\tprocess = command.exe\n',
        '[diff "evil"]\n\ttextconv = command.exe\n',
        "[core]\n\tworktree = C:/evil\n",
    ],
)
def test_git_local_config_rejects_include_filter_diff_and_worktree(tmp_path, config):
    root = tmp_path / "repo"
    metadata = root / ".git"
    metadata.mkdir(parents=True)
    (metadata / "config").write_text(config, encoding="utf-8")
    assert _local_git_config_safe(root) is False


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_repository_preflight_rejects_non_plain_index_flags(tmp_path, flag):
    root = tmp_path / "index-flags"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "index@example.invalid")
    _git(root, "config", "user.name", "Index Verifier")
    _write(root / "tracked.txt", "tracked\n")
    _commit(root, "tracked")
    _git(root, "update-index", flag, "tracked.txt")
    native_git = _native_git_executable()
    runner = TrustedGitRunner(native_git, file_identity(native_git)["sha256"])
    with pytest.raises(PublicationVerificationError) as raised:
        runner.assert_repository_safe(root)
    _assert_code(raised, "GIT_INDEX_FLAGS_UNSAFE")


def test_nul_changed_paths_preserve_newline_nonascii_and_leading_dash_boundaries():
    class FakeRunner:
        def __init__(self):
            self.args = None

        def run(self, root, args, *, check=True, binary=False):
            self.args = tuple(args)
            return subprocess.CompletedProcess(
                args=list(args), returncode=0, stdout="-leading\nname\0证据.json\0".encode(), stderr=b""
            )

    runner = FakeRunner()
    paths = _changed_paths(runner, Path("."), "a" * 40, "b" * 40)
    assert paths == {"-leading\nname", "证据.json"}
    assert runner.args == (
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        "a" * 40,
        "b" * 40,
        "--",
    )


def test_real_git_nul_paths_round_trip_special_filenames(tmp_path):
    root = tmp_path / "special-path-repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "paths@example.invalid")
    _git(root, "config", "user.name", "Path Verifier")
    _write(root / "base.txt", "base\n")
    start, _ = _commit(root, "base")
    special_paths = {"-leading.txt", "docs/nonascii-证据.json"}
    for relative in special_paths:
        _write(root / relative, "safe\n")
    newline_path = "docs/line\nbreak.txt"
    try:
        _write(root / newline_path, "safe\n")
    except OSError:
        pass  # Windows rejects this filename; raw NUL parsing is covered above.
    else:
        special_paths.add(newline_path)
    end, _ = _commit(root, "special paths")
    native_git = _native_git_executable()
    runner = TrustedGitRunner(native_git, file_identity(native_git)["sha256"])
    assert _changed_paths(runner, root, start, end) == special_paths
    for relative in special_paths:
        assert _git_regular_file_blob(runner, root, end, relative) == b"safe\n"


def test_git_revision_and_path_commands_have_explicit_option_boundaries():
    class FakeRunner:
        def __init__(self):
            self.calls = []

        def run(self, root, args, *, check=True, binary=False):
            self.calls.append(tuple(args))
            if args[0] == "rev-parse":
                return subprocess.CompletedProcess(args, 0, "a" * 40 + "\n", "")
            if args[0] == "ls-tree":
                return subprocess.CompletedProcess(
                    args,
                    0,
                    ("100644 blob " + "b" * 40 + "\t-leading\0").encode(),
                    b"",
                )
            return subprocess.CompletedProcess(args, 0, b"blob", b"")

    runner = FakeRunner()
    assert _resolve_commit(runner, Path("."), "refs/tags/safe") == "a" * 40
    assert _git_regular_file_blob(runner, Path("."), "a" * 40, "-leading") == b"blob"
    assert runner.calls[0][0:4] == (
        "rev-parse",
        "--verify",
        "--end-of-options",
        "refs/tags/safe^{commit}",
    )
    assert runner.calls[1][-2:] == ("--", "-leading")
    assert runner.calls[1][0:2] == ("ls-tree", "-z")


def test_changed_path_output_rejects_non_utf8_and_missing_nul_terminator():
    class FakeRunner:
        def __init__(self, stdout):
            self.stdout = stdout

        def run(self, root, args, *, check=True, binary=False):
            return subprocess.CompletedProcess(args, 0, self.stdout, b"")

    with pytest.raises(PublicationVerificationError) as raised:
        _changed_paths(FakeRunner(b"\xff\0"), Path("."), "a" * 40, "b" * 40)
    _assert_code(raised, "GIT_PATH_NOT_UTF8")
    with pytest.raises(PublicationVerificationError) as raised:
        _changed_paths(FakeRunner(b"path-without-nul"), Path("."), "a" * 40, "b" * 40)
    _assert_code(raised, "GIT_NUL_PATH_OUTPUT_INVALID")


def test_service_blocks_missing_or_misbinding_external_final_receipt(publication_repo):
    with pytest.raises(PublicationVerificationError) as raised:
        verify_publication(
            publication_repo["manifest"],
            publication_repo["index"],
            root=publication_repo["root"],
            manifest_path=MANIFEST_PATH,
            index_path=INDEX_PATH,
            trusted_public_keys=publication_repo["trusted_public_keys"],
            final_ref_receipt=None,
            git_runner=publication_repo["git_runner"],
        )
    _assert_code(raised, "EXTERNAL_FINAL_REF_RECEIPT_REQUIRED")

    receipt = deepcopy(publication_repo["final_ref_receipt"])
    receipt["signed_payload"]["final_sha"] = "0" * 40
    with pytest.raises(PublicationVerificationError) as raised:
        verify_publication(
            publication_repo["manifest"],
            publication_repo["index"],
            root=publication_repo["root"],
            manifest_path=MANIFEST_PATH,
            index_path=INDEX_PATH,
            trusted_public_keys=publication_repo["trusted_public_keys"],
            final_ref_receipt=receipt,
            git_runner=publication_repo["git_runner"],
        )
    _assert_code(raised, "EXTERNAL_FINAL_REF_RECEIPT_PAYLOAD_MISMATCH")


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda value: value["entries"].append(deepcopy(value["entries"][0])),
            "EVIDENCE_ID_DUPLICATE",
        ),
        (
            lambda value: value["entries"][0].update({"path": "../evidence.json"}),
            "EVIDENCE_PATH_UNSAFE_OR_MISSING",
        ),
        (
            lambda value: value["entries"][0].update({"path": INDEX_PATH}),
            "INDEX_SELF_OR_MANIFEST_REFERENCE",
        ),
        (
            lambda value: value["entries"][0].update({"path": MANIFEST_PATH}),
            "INDEX_SELF_OR_MANIFEST_REFERENCE",
        ),
        (
            lambda value: value["entries"][0].update(
                {"sensitivity": "COORDINATOR_ONLY"}
            ),
            "EVIDENCE_SENSITIVITY_INVALID",
        ),
    ],
)
def test_index_rejects_duplicate_unsafe_self_manifest_and_coordinator_only(
    publication_repo, mutate, code
):
    index = deepcopy(publication_repo["index"])
    mutate(index)
    index["entry_count"] = len(index["entries"])
    index["bundle_sha256"] = evidence_bundle_sha256(index["entries"])

    with pytest.raises(PublicationVerificationError) as raised:
        validate_evidence_index(
            index,
            root=publication_repo["root"],
            index_path=INDEX_PATH,
            manifest_path=MANIFEST_PATH,
        )
    _assert_code(raised, code)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("bytes", 1, "EVIDENCE_SIZE_MISMATCH"),
        ("sha256", "0" * 64, "EVIDENCE_HASH_MISMATCH"),
        ("contains_secrets", True, "EVIDENCE_SECRET_FLAG_INVALID"),
        ("contains_real_candidate_data", True, "REAL_CANDIDATE_DATA_PRESENT"),
        ("publishable", False, "EVIDENCE_NOT_PUBLISHABLE"),
        ("redaction_status", "NOT_RUN", "EVIDENCE_REDACTION_INVALID"),
    ],
)
def test_index_rejects_identity_and_publication_boundary_drift(
    publication_repo, field, value, code
):
    index = deepcopy(publication_repo["index"])
    index["entries"][0][field] = value
    index["bundle_sha256"] = evidence_bundle_sha256(index["entries"])

    with pytest.raises(PublicationVerificationError) as raised:
        validate_evidence_index(
            index,
            root=publication_repo["root"],
            index_path=INDEX_PATH,
            manifest_path=MANIFEST_PATH,
        )
    _assert_code(raised, code)


def test_safe_repo_file_rejects_symlink_or_reparse_components(
    publication_repo, monkeypatch
):
    root = publication_repo["root"]
    monkeypatch.setattr(
        "scripts.verify_interview_quality_v1_publication._is_link_or_reparse",
        lambda path: path.name == "t65-formal-provider.json",
    )
    assert safe_repo_file(root, EVIDENCE_PATH) is None


@pytest.mark.parametrize(
    ("metric", "code"),
    [
        ({"status": "NOT_RUN", "value": 0}, "NOT_RUN_VALUE_MUST_BE_NULL:$.metrics.cost"),
        (
            {"status": "BLOCKED", "value": None},
            "NOT_RUN_REASON_REQUIRED:$.metrics.cost",
        ),
    ],
)
def test_not_run_and_blocked_require_null_value_and_reason(
    publication_repo, metric, code
):
    manifest = deepcopy(publication_repo["manifest"])
    manifest["metrics"]["cost"] = metric

    with pytest.raises(PublicationVerificationError) as raised:
        _verify(publication_repo, manifest=manifest)
    _assert_code(raised, code)


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("postgresql://user:password@localhost/interview", "CONNECTION_SECRET_PRESENT"),
        ("-----BEGIN PRIVATE KEY-----\nsecret", "PRIVATE_KEY_PRESENT"),
        ("OPENAI_API_KEY=sk-live-value", "API_KEY_VALUE_PRESENT"),
        (r"C:\Users\admin\private.json", "MACHINE_SPECIFIC_PATH_PRESENT"),
        ("/home/operator/private.json", "MACHINE_SPECIFIC_PATH_PRESENT"),
        (r"\\server\private-share\evidence.json", "MACHINE_SPECIFIC_PATH_PRESENT"),
        ('{"api_key":"opaque-provider-secret"}', "SENSITIVE_FIELD_VALUE_PRESENT"),
    ],
)
def test_indexed_public_text_rejects_secrets_and_machine_paths(
    publication_repo, content, code
):
    root = publication_repo["root"]
    (root / EVIDENCE_PATH).write_text(content, encoding="utf-8")
    index = deepcopy(publication_repo["index"])
    index["entries"][0].update(file_identity(root / EVIDENCE_PATH))
    index["bundle_sha256"] = evidence_bundle_sha256(index["entries"])

    with pytest.raises(PublicationVerificationError) as raised:
        validate_evidence_index(
            index,
            root=root,
            index_path=INDEX_PATH,
            manifest_path=MANIFEST_PATH,
        )
    _assert_code(raised, code)


@pytest.mark.parametrize(
    "content",
    [
        r"D:\workspace\quality\manifest.json",
        "F:/agent/Interview-Agent/evidence.json",
        r"\\?\C:\private\evidence.json",
        r"\\.\pipe\private-evidence",
        r"\\server\private-share\evidence.json",
        "file:///C:/private/evidence.json",
        "file:///F:/literal",
        "file:///home/operator/evidence.json",
        "file://server/private-share/evidence.json",
        "/home/operator/evidence.json",
        "/Users/operator/evidence.json",
        "/tmp/private-evidence.json",
        "/workspace/private-evidence.json",
        "/var/lib/private-evidence.json",
        "/opt/private-evidence.json",
        "/mnt/private/evidence.json",
        "/root/private-evidence.json",
        "/srv/private-evidence.json",
        "/data/private-evidence.json",
        "/run/private-evidence.json",
        "/etc/private-evidence.json",
        "/usr/local/private-evidence.json",
        "/private/var/private-evidence.json",
        "/Volumes/private/evidence.json",
    ],
)
def test_machine_path_helper_rejects_absolute_host_locations(content):
    assert _contains_machine_specific_path(content)


@pytest.mark.parametrize(
    "content",
    [
        "https://example.com/home/operator/evidence.json",
        "https://example.com/F:/literal",
        "https://example.com/search?next=F:/literal",
        "s3://bucket/F:/key",
        "/api/interviews/session-1/report",
        r"C:relative\evidence.json",
        "release version 1.2.3 and schema v2.0",
        "docs/interview-quality-v1-publication-evidence/evidence.json",
    ],
)
def test_machine_path_helper_allows_non_machine_path_text(content):
    assert not _contains_machine_specific_path(content)


@pytest.mark.parametrize(
    "content",
    [
        r"D:\workspace\quality\manifest.json",
        "file:///home/operator/evidence.json",
        "/var/lib/private/evidence.json",
    ],
)
def test_index_validation_rejects_expanded_machine_path_forms(
    publication_repo, content
):
    root = publication_repo["root"]
    index = _write_indexed_evidence_note(publication_repo, content)

    with pytest.raises(PublicationVerificationError) as raised:
        validate_evidence_index(
            index,
            root=root,
            index_path=INDEX_PATH,
            manifest_path=MANIFEST_PATH,
        )
    _assert_code(raised, "MACHINE_SPECIFIC_PATH_PRESENT")


@pytest.mark.parametrize(
    "content",
    [
        "https://example.com/home/operator/evidence.json",
        "https://example.com/F:/literal",
        "https://example.com/search?next=F:/literal",
        "s3://bucket/F:/key",
        "/api/interviews/session-1/report",
        r"C:relative\evidence.json",
        "release version 1.2.3",
        "docs/interview-quality-v1-publication-evidence/evidence.json",
    ],
)
def test_index_validation_allows_non_machine_path_text(publication_repo, content):
    root = publication_repo["root"]
    index = _write_indexed_evidence_note(publication_repo, content)

    result = validate_evidence_index(
        index,
        root=root,
        index_path=INDEX_PATH,
        manifest_path=MANIFEST_PATH,
    )
    assert result["entries_verified"] == len(REQUIRED_EVIDENCE_KINDS)


@pytest.mark.parametrize(
    "content",
    [
        "F:/agent/Interview-Agent/publication.json",
        r"\\?\C:\private\publication.json",
        "file:///workspace/private/publication.json",
        "/Volumes/private/publication.json",
    ],
)
def test_manifest_rejects_expanded_machine_path_forms(publication_repo, content):
    manifest = deepcopy(publication_repo["manifest"])
    manifest["publication_note"] = content

    with pytest.raises(PublicationVerificationError) as raised:
        _verify(publication_repo, manifest=manifest)
    _assert_code(raised, "MACHINE_SPECIFIC_PATH_PRESENT")


@pytest.mark.parametrize(
    "content",
    [
        "https://example.com/home/operator/publication.json",
        "https://example.com/F:/literal",
        "https://example.com/search?next=F:/literal",
        "s3://bucket/F:/key",
        "/api/publication/status",
        r"D:relative\publication.json",
        "schema version 1.2.3",
        "docs/interview-quality-v1-execution-manifest.json",
    ],
)
def test_manifest_allows_non_machine_path_text(publication_repo, content):
    manifest = deepcopy(publication_repo["manifest"])
    manifest["publication_note"] = content

    assert _verify(publication_repo, manifest=manifest)["status"] == "PASS"


def test_redacted_secret_field_is_safe_public_metadata(publication_repo):
    root = publication_repo["root"]
    index = deepcopy(publication_repo["index"])
    entry = index["entries"][0]
    _write_json(
        root / EVIDENCE_PATH,
        {
            "api_key": "REDACTED",
            "evidence_id": entry["evidence_id"],
            "kind": entry["kind"],
            "status": "PASS",
            "bound_revision": entry["bound_revision"],
            "bound_tree": entry["bound_tree"],
        },
    )
    index["entries"][0].update(file_identity(root / EVIDENCE_PATH))
    index["bundle_sha256"] = evidence_bundle_sha256(index["entries"])

    result = validate_evidence_index(
        index,
        root=root,
        index_path=INDEX_PATH,
        manifest_path=MANIFEST_PATH,
    )
    assert result["entries_verified"] == len(REQUIRED_EVIDENCE_KINDS)


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda value: value["revisions"]["implementation"].update(
                {"tree": "0" * 40}
            ),
            "IMPLEMENTATION_TREE_MISMATCH",
        ),
        (
            lambda value: value["revisions"]["publication"].update(
                {"sha": value["revisions"]["implementation"]["sha"]}
            ),
            "PUBLICATION_REVISION_MISMATCH",
        ),
        (
            lambda value: value["revisions"]["final"].update(
                {"sha": "0" * 40}
            ),
            "FINAL_SELF_HASH_FORBIDDEN",
        ),
        (
            lambda value: value.update(
                {"publication_commit_self_hash_recorded": True}
            ),
            "PUBLICATION_SELF_HASH_FLAG_INVALID",
        ),
    ],
)
def test_revision_tree_ref_and_self_hash_tampering_is_rejected(
    publication_repo, mutate, code
):
    manifest = deepcopy(publication_repo["manifest"])
    mutate(manifest)

    with pytest.raises(PublicationVerificationError) as raised:
        _verify(publication_repo, manifest=manifest)
    _assert_code(raised, code)


def test_final_diff_rejects_production_code(publication_repo):
    root = publication_repo["root"]
    _write(root / "app/late-fix.py", "LATE_FIX = True\n")
    late_sha, _ = _commit(root, "forbidden late production fix")
    _git(root, "tag", "-f", FINAL_REF.removeprefix("refs/tags/"), late_sha)

    with pytest.raises(PublicationVerificationError) as raised:
        _verify(publication_repo)
    _assert_code(raised, "FINAL_DIFF_NOT_ALLOWLISTED")


def test_final_diff_rejects_unindexed_docs_secret_file(publication_repo):
    root = publication_repo["root"]
    _write(root / "docs/public-notes.txt", "OPENAI_API_KEY=sk-must-not-publish\n")
    late_sha, _ = _commit(root, "unindexed secret publication file")
    _git(root, "tag", "-f", FINAL_REF.removeprefix("refs/tags/"), late_sha)

    with pytest.raises(PublicationVerificationError) as raised:
        _verify(publication_repo)

    _assert_code(raised, "FINAL_DIFF_NOT_ALLOWLISTED")
    _assert_code(raised, "FINAL_CHANGED_FILE_NOT_INDEXED")
    _assert_code(raised, "API_KEY_VALUE_PRESENT")


def test_fixed_public_control_file_is_scanned_for_sensitive_content(publication_repo):
    root = publication_repo["root"]
    _write(root / "README.md", "unseal_token=do-not-publish\n")
    late_sha, _ = _commit(root, "unsafe public control content")
    _git(root, "tag", "-f", FINAL_REF.removeprefix("refs/tags/"), late_sha)

    with pytest.raises(PublicationVerificationError) as raised:
        _verify(publication_repo)

    _assert_code(raised, "SENSITIVE_VALUE_PRESENT")
    assert "FINAL_CHANGED_FILE_NOT_INDEXED" not in raised.value.codes


def test_sensitive_path_fragments_are_denied_even_for_docs(publication_repo):
    root = publication_repo["root"]
    _write(root / "docs/unblind-map.txt", "sanitized placeholder\n")
    late_sha, _ = _commit(root, "forbidden publication path")
    _git(root, "tag", "-f", FINAL_REF.removeprefix("refs/tags/"), late_sha)

    with pytest.raises(PublicationVerificationError) as raised:
        _verify(publication_repo)

    _assert_code(raised, "FINAL_CHANGED_PATH_DENIED")
    _assert_code(raised, "FINAL_CHANGED_FILE_NOT_INDEXED")


def test_manifest_index_reference_uses_raw_bytes_size_and_sha(publication_repo):
    manifest = deepcopy(publication_repo["manifest"])
    manifest["evidence_index"]["sha256"] = "0" * 64
    manifest["evidence_index"]["bytes"] = 1

    with pytest.raises(PublicationVerificationError) as raised:
        _verify(publication_repo, manifest=manifest)
    _assert_code(raised, "MANIFEST_INDEX_HASH_MISMATCH")
    _assert_code(raised, "MANIFEST_INDEX_SIZE_MISMATCH")


@pytest.mark.parametrize(
    ("target", "field", "value", "code"),
    [
        ("index", "implementation_sha", "0" * 40, "INDEX_IMPLEMENTATION_SHA_MISMATCH"),
        (
            "index",
            "implementation_tree",
            "0" * 40,
            "INDEX_IMPLEMENTATION_TREE_MISMATCH",
        ),
        (
            "entry",
            "bound_revision",
            "0" * 40,
            "EVIDENCE_IMPLEMENTATION_SHA_MISMATCH",
        ),
        (
            "entry",
            "bound_tree",
            "0" * 40,
            "EVIDENCE_IMPLEMENTATION_TREE_MISMATCH",
        ),
    ],
)
def test_index_and_each_evidence_entry_bind_the_implementation_revision(
    publication_repo, target, field, value, code
):
    index = deepcopy(publication_repo["index"])
    if target == "index":
        index[field] = value
    else:
        index["entries"][0][field] = value
        index["bundle_sha256"] = evidence_bundle_sha256(index["entries"])

    with pytest.raises(PublicationVerificationError) as raised:
        _verify(publication_repo, index=index)
    _assert_code(raised, code)


def test_worktree_evidence_drift_cannot_masquerade_as_the_final_ref(publication_repo):
    root = publication_repo["root"]
    _write(root / EVIDENCE_PATH, '{"status":"PASS","changed":true}\n')
    index = deepcopy(publication_repo["index"])
    index["entries"][0].update(file_identity(root / EVIDENCE_PATH))
    index["bundle_sha256"] = evidence_bundle_sha256(index["entries"])

    with pytest.raises(PublicationVerificationError) as raised:
        _verify(publication_repo, index=index)
    _assert_code(raised, "FINAL_EVIDENCE_BLOB_MISMATCH")


def test_missing_acceptance_is_blocked(publication_repo):
    manifest = deepcopy(publication_repo["manifest"])
    manifest.pop("acceptance")
    with pytest.raises(PublicationVerificationError) as raised:
        _verify(publication_repo, manifest=manifest)
    _assert_code(raised, "ACCEPTANCE_REQUIRED")


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda a: a.update({"engineering_status": "BLOCKED"}), "ENGINEERING_NOT_PASS"),
        (lambda a: a.update({"quality_status": "BLOCKED"}), "QUALITY_NOT_PASS"),
        (lambda a: a.update({"final_acceptance": "BLOCKED"}), "FINAL_ACCEPTANCE_NOT_PASS"),
        (lambda a: a.update({"t65_formal_provider_status": "NOT_RUN"}), "T65_FORMAL_PROVIDER_NOT_PASS"),
        (lambda a: a["independent_human_review"].update({"reviewer_independent": False}), "INDEPENDENT_HUMAN_REVIEW_NOT_PASS"),
        (lambda a: a["independent_human_review"].update({"signature_base64": base64.b64encode(b"invalid").decode("ascii")}), "INDEPENDENT_HUMAN_REVIEW_SIGNATURE_INVALID"),
        (lambda a: a["required_tests"].update({"failed": 1}), "REQUIRED_TESTS_NOT_PASS"),
        (lambda a: a.update({"blocking_skips": 1}), "BLOCKING_SKIPS_NONZERO"),
        (lambda a: a["unresolved_findings"].update({"p0": 1}), "UNRESOLVED_P0_NONZERO"),
        (lambda a: a["unresolved_findings"].update({"p1": 1}), "UNRESOLVED_P1_NONZERO"),
    ],
)
def test_acceptance_is_derived_from_strict_pass_contract(publication_repo, mutate, code):
    manifest = deepcopy(publication_repo["manifest"])
    mutate(manifest["acceptance"])
    with pytest.raises(PublicationVerificationError) as raised:
        _verify(publication_repo, manifest=manifest)
    _assert_code(raised, code)


def test_empty_index_and_missing_required_kinds_are_blocked(publication_repo):
    index = _index(
        [],
        implementation_sha=publication_repo["implementation_sha"],
        implementation_tree=publication_repo["implementation_tree"],
    )
    with pytest.raises(PublicationVerificationError) as raised:
        validate_evidence_index(
            index,
            root=publication_repo["root"],
            index_path=INDEX_PATH,
            manifest_path=MANIFEST_PATH,
        )
    _assert_code(raised, "INDEX_ENTRIES_EMPTY")
    assert any(code.startswith("REQUIRED_EVIDENCE_KINDS_MISSING:") for code in raised.value.codes)


def test_evidence_directory_and_index_are_bidirectionally_complete(publication_repo):
    root = publication_repo["root"]
    _write(root / EVIDENCE_DIR / "unindexed.json", '{"status":"PASS"}\n')
    with pytest.raises(PublicationVerificationError) as raised:
        validate_evidence_index(
            publication_repo["index"],
            root=root,
            index_path=INDEX_PATH,
            manifest_path=MANIFEST_PATH,
        )
    _assert_code(raised, "EVIDENCE_DIRECTORY_INDEX_MISMATCH")


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/release.yml",
        "Dockerfile",
        "pyproject.toml",
        "deploy/release.ps1",
        "scripts/publish.py",
        "tests/test_release.py",
        "src/runtime.py",
    ],
)
def test_publication_allowlist_defaults_to_deny_for_executable_build_paths(path):
    assert not _publication_path_allowed(
        path,
        allowed_files=frozenset({"README.md"}),
        allowed_prefixes=(f"{EVIDENCE_DIR}/",),
        indexed_files=frozenset({path.casefold()}),
    )


def test_final_must_be_immediate_single_parent_of_publication(publication_repo):
    root = publication_repo["root"]
    _write(root / MANIFEST_PATH, json.dumps(publication_repo["manifest"], sort_keys=True) + "\n")
    late_sha, _ = _commit(root, "extra final commit")
    _git(root, "tag", "-f", FINAL_REF.removeprefix("refs/tags/"), late_sha)
    with pytest.raises(PublicationVerificationError) as raised:
        _verify(publication_repo)
    _assert_code(raised, "FINAL_PARENT_NOT_PUBLICATION")


def test_publication_must_be_immediate_single_parent_of_implementation(publication_repo):
    root = publication_repo["root"]
    manifest = deepcopy(publication_repo["manifest"])
    manifest["revisions"]["publication"].update(
        {"sha": publication_repo["final_sha"], "tree": publication_repo["final_tree"]}
    )
    _git(root, "tag", "-f", PUBLICATION_REF.removeprefix("refs/tags/"), publication_repo["final_sha"])
    with pytest.raises(PublicationVerificationError) as raised:
        _verify(publication_repo, manifest=manifest)
    _assert_code(raised, "PUBLICATION_PARENT_NOT_IMPLEMENTATION")


def test_publication_evidence_is_byte_frozen_before_final(publication_repo):
    root = publication_repo["root"]
    _write(root / EVIDENCE_PATH, '{"status":"PASS","rewritten_at_final":true}\n')
    late_sha, _ = _commit(root, "rewrite frozen evidence after publication")
    _git(root, "tag", "-f", FINAL_REF.removeprefix("refs/tags/"), late_sha)
    with pytest.raises(PublicationVerificationError) as raised:
        _verify(publication_repo)
    _assert_code(raised, "PUBLICATION_ARTIFACT_CHANGED_AFTER_FREEZE")


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ('{"candidate_answer":"private answer"}', "SENSITIVE_FIELD_VALUE_PRESENT"),
        ('{"resume_text":"private resume"}', "SENSITIVE_FIELD_VALUE_PRESENT"),
        ('{"job_description":"private JD"}', "SENSITIVE_FIELD_VALUE_PRESENT"),
        ('{"raw_response":"provider output"}', "SENSITIVE_FIELD_VALUE_PRESENT"),
        ('{"prompt":"private prompt"}', "SENSITIVE_FIELD_VALUE_PRESENT"),
        ('{"messages":[{"role":"user","content":"private"}]}', "SENSITIVE_FIELD_VALUE_PRESENT"),
        ('{"authorization":"opaque credential"}', "SENSITIVE_FIELD_VALUE_PRESENT"),
        ('{"access_token":"opaque token"}', "SENSITIVE_FIELD_VALUE_PRESENT"),
        ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz", "BEARER_TOKEN_PRESENT"),
        ("eyJabcdefghijk.abcdefghijkl.abcdefghijkl", "JWT_TOKEN_PRESENT"),
    ],
)
def test_candidate_provider_and_token_material_is_rejected(publication_repo, content, code):
    root = publication_repo["root"]
    _write(root / EVIDENCE_PATH, content)
    index = deepcopy(publication_repo["index"])
    index["entries"][0].update(file_identity(root / EVIDENCE_PATH))
    index["bundle_sha256"] = evidence_bundle_sha256(index["entries"])
    with pytest.raises(PublicationVerificationError) as raised:
        validate_evidence_index(
            index,
            root=root,
            index_path=INDEX_PATH,
            manifest_path=MANIFEST_PATH,
        )
    _assert_code(raised, code)
