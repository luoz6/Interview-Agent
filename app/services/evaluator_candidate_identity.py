from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess

from app.runtime.config.environment import process_environment


EVALUATOR_CANDIDATE_IDENTITY_VERSION = "evaluator-candidate-identity-v1"
_UNTRACKED_MANIFEST_VERSION = "evaluator-untracked-safe-manifest-v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40,64}$")
_MAX_UNTRACKED_SAFE_BYTES = 4 * 1024 * 1024
_SAFE_TOP_LEVEL_DIRECTORIES = frozenset(
    {
        "app",
        "config",
        "contracts",
        "docs",
        "eval",
        "frontend",
        "scripts",
        "tests",
    }
)
_SAFE_UNTRACKED_SUFFIXES = frozenset(
    {
        ".c",
        ".cfg",
        ".cpp",
        ".css",
        ".go",
        ".h",
        ".html",
        ".ini",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".kt",
        ".kts",
        ".md",
        ".php",
        ".ps1",
        ".py",
        ".pyi",
        ".rb",
        ".rs",
        ".rst",
        ".scss",
        ".sh",
        ".sql",
        ".svelte",
        ".swift",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".vue",
        ".yaml",
        ".yml",
    }
)
_BLOCKED_UNTRACKED_NAMES = frozenset(
    {
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "secrets",
        "secrets.json",
    }
)
_BLOCKED_UNTRACKED_SUFFIXES = frozenset(
    {".cer", ".crt", ".jks", ".key", ".keystore", ".p12", ".pem", ".pfx"}
)


@dataclass(frozen=True)
class EvaluatorCandidateIdentity:
    candidate_revision: str
    candidate_tree: str
    worktree_clean: bool
    candidate_unstaged_tracked_diff_sha256: str | None
    candidate_staged_diff_sha256: str | None
    candidate_untracked_safe_manifest_sha256: str | None
    candidate_untracked_safe_file_count: int
    candidate_untracked_excluded_file_count: int
    candidate_identity_version: str = EVALUATOR_CANDIDATE_IDENTITY_VERSION

    def __post_init__(self) -> None:
        if self.candidate_identity_version != EVALUATOR_CANDIDATE_IDENTITY_VERSION:
            raise ValueError("unsupported evaluator candidate identity version")
        if _GIT_OBJECT.fullmatch(self.candidate_revision) is None:
            raise ValueError("candidate_revision is not a Git object identity")
        if _GIT_OBJECT.fullmatch(self.candidate_tree) is None:
            raise ValueError("candidate_tree is not a Git object identity")
        if self.candidate_untracked_safe_file_count < 0:
            raise ValueError("candidate_untracked_safe_file_count must be non-negative")
        if self.candidate_untracked_excluded_file_count < 0:
            raise ValueError(
                "candidate_untracked_excluded_file_count must be non-negative"
            )
        digests = (
            self.candidate_unstaged_tracked_diff_sha256,
            self.candidate_staged_diff_sha256,
            self.candidate_untracked_safe_manifest_sha256,
        )
        if self.worktree_clean:
            if any(value is not None for value in digests):
                raise ValueError("clean candidate diff identities must be null")
            if (
                self.candidate_untracked_safe_file_count != 0
                or self.candidate_untracked_excluded_file_count != 0
            ):
                raise ValueError("clean candidate untracked counts must be zero")
        elif any(value is None or _DIGEST.fullmatch(value) is None for value in digests):
            raise ValueError("dirty candidate diff identities must be SHA-256 values")

    def manifest_fields(self) -> dict[str, object]:
        return {
            "candidate_identity_version": self.candidate_identity_version,
            "candidate_revision": self.candidate_revision,
            "candidate_tree": self.candidate_tree,
            "worktree_clean": self.worktree_clean,
            "candidate_unstaged_tracked_diff_sha256": (
                self.candidate_unstaged_tracked_diff_sha256
            ),
            "candidate_staged_diff_sha256": self.candidate_staged_diff_sha256,
            "candidate_untracked_safe_manifest_sha256": (
                self.candidate_untracked_safe_manifest_sha256
            ),
            "candidate_untracked_safe_file_count": (
                self.candidate_untracked_safe_file_count
            ),
            "candidate_untracked_excluded_file_count": (
                self.candidate_untracked_excluded_file_count
            ),
        }


def capture_evaluator_candidate_identity(root: Path) -> EvaluatorCandidateIdentity:
    repository_root = root.resolve(strict=True)
    discovered_root = Path(
        _git_text(repository_root, "rev-parse", "--show-toplevel")
    ).resolve(strict=True)
    if discovered_root != repository_root:
        raise ValueError("candidate identity root must be the Git worktree root")

    revision = _git_text(
        repository_root, "rev-parse", "--verify", "HEAD^{commit}"
    ).casefold()
    tree = _git_text(repository_root, "rev-parse", "--verify", "HEAD^{tree}").casefold()
    status = _worktree_status(repository_root)
    for _ in range(2):
        unstaged = _tracked_diff(repository_root, staged=False)
        staged = _tracked_diff(repository_root, staged=True)
        untracked = _git_bytes(
            repository_root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        )
        refreshed_status = _worktree_status(repository_root)
        if refreshed_status == status:
            status = refreshed_status
            break
        # Git can refresh a racily-clean index entry on the first status/diff
        # read (notably on Windows with core.autocrlf). Re-read the complete
        # identity surface so hashes and the final clean bit describe one
        # stable view rather than the pre-refresh cache state.
        status = refreshed_status
    else:
        raise RuntimeError("candidate worktree changed during identity capture")
    final_revision = _git_text(
        repository_root, "rev-parse", "--verify", "HEAD^{commit}"
    ).casefold()
    final_tree = _git_text(
        repository_root, "rev-parse", "--verify", "HEAD^{tree}"
    ).casefold()
    if final_revision != revision or final_tree != tree:
        raise RuntimeError("candidate HEAD changed during identity capture")
    clean = not status
    if clean:
        return EvaluatorCandidateIdentity(
            candidate_revision=revision,
            candidate_tree=tree,
            worktree_clean=True,
            candidate_unstaged_tracked_diff_sha256=None,
            candidate_staged_diff_sha256=None,
            candidate_untracked_safe_manifest_sha256=None,
            candidate_untracked_safe_file_count=0,
            candidate_untracked_excluded_file_count=0,
        )

    records, excluded_count = _safe_untracked_records(repository_root, untracked)
    untracked_manifest = json.dumps(
        {
            "schema_version": _UNTRACKED_MANIFEST_VERSION,
            "files": records,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return EvaluatorCandidateIdentity(
        candidate_revision=revision,
        candidate_tree=tree,
        worktree_clean=False,
        candidate_unstaged_tracked_diff_sha256=_sha256(unstaged),
        candidate_staged_diff_sha256=_sha256(staged),
        candidate_untracked_safe_manifest_sha256=_sha256(untracked_manifest),
        candidate_untracked_safe_file_count=len(records),
        candidate_untracked_excluded_file_count=excluded_count,
    )


def _safe_untracked_records(
    root: Path,
    raw_listing: bytes,
) -> tuple[list[dict[str, object]], int]:
    records: list[dict[str, object]] = []
    excluded_count = 0
    raw_paths = sorted(path for path in raw_listing.split(b"\0") if path)
    for raw_path in raw_paths:
        try:
            relative = raw_path.decode("utf-8")
        except UnicodeDecodeError:
            excluded_count += 1
            continue
        if not _is_safe_untracked_relative_path(relative):
            excluded_count += 1
            continue
        candidate = root.joinpath(*PurePosixPath(relative).parts)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            file_stat = candidate.lstat()
            if (
                stat.S_ISLNK(file_stat.st_mode)
                or not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_size > _MAX_UNTRACKED_SAFE_BYTES
            ):
                excluded_count += 1
                continue
            content_sha256 = _sha256(candidate.read_bytes())
        except (OSError, ValueError):
            excluded_count += 1
            continue
        records.append(
            {
                "path": PurePosixPath(relative).as_posix(),
                "bytes": file_stat.st_size,
                "content_sha256": content_sha256,
            }
        )
    return records, excluded_count


def _is_safe_untracked_relative_path(value: str) -> bool:
    if not value or "\x00" in value or "\\" in value:
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        return False
    folded_parts = tuple(part.casefold() for part in path.parts)
    if any(part in {"", ".", ".git"} for part in folded_parts):
        return False
    if folded_parts[0] not in _SAFE_TOP_LEVEL_DIRECTORIES:
        return False
    name = folded_parts[-1]
    suffix = path.suffix.casefold()
    if name.startswith(".env") or name in _BLOCKED_UNTRACKED_NAMES:
        return False
    if suffix in _BLOCKED_UNTRACKED_SUFFIXES:
        return False
    return suffix in _SAFE_UNTRACKED_SUFFIXES


def _git_text(root: Path, *args: str) -> str:
    return _git_bytes(root, *args).decode("ascii").strip()


def _tracked_diff(root: Path, *, staged: bool) -> bytes:
    args = ["diff"]
    if staged:
        args.append("--cached")
    args.extend(
        [
            "--binary",
            "--full-index",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--submodule=short",
            "--",
        ]
    )
    return _git_bytes(root, *args)


def _worktree_status(root: Path) -> bytes:
    return _git_bytes(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )


def _git_bytes(root: Path, *args: str) -> bytes:
    environment = dict(process_environment())
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "LC_ALL": "C",
            "LANG": "C",
        }
    )
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=root,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        shell=False,
        timeout=30,
    )
    return result.stdout


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
