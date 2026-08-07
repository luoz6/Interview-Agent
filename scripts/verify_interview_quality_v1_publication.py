from __future__ import annotations

import argparse
import base64
import binascii
from dataclasses import dataclass, field as dataclass_field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from types import MappingProxyType
from typing import Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


INDEX_SCHEMA = "interview-quality-v1-evidence-index-v1"
MANIFEST_SCHEMA = "interview-quality-v1-publication-manifest-v1"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_REF = re.compile(r"^refs/tags/[a-z0-9][a-z0-9._/-]*$")
TRUSTED_PUBLICATION_AUTHORITY_PUBLIC_KEYS: Mapping[str, bytes] = MappingProxyType({})
_NATIVE_GIT_CANDIDATES = (
    Path("E:/Git/mingw64/bin/git.exe"),
    Path("C:/Program Files/Git/mingw64/bin/git.exe"),
    Path("C:/Program Files (x86)/Git/mingw64/bin/git.exe"),
    Path("/usr/bin/git"),
    Path("/usr/local/bin/git"),
    Path("/opt/homebrew/bin/git"),
)
TRUSTED_GIT_EXECUTABLE_SHA256_BY_PATH: Mapping[str, frozenset[str]] = (
    MappingProxyType({})
)
_GIT_ENV_ALLOWLIST = frozenset({"SYSTEMDRIVE", "SYSTEMROOT", "WINDIR"})
PUBLICATION_EVIDENCE_PREFIX = "docs/interview-quality-v1-publication-evidence/"
DEFAULT_ALLOWED_PUBLICATION_FILES = frozenset({"README.md"})
DEFAULT_ALLOWED_PUBLICATION_PREFIXES = (PUBLICATION_EVIDENCE_PREFIX,)
REQUIRED_EVIDENCE_KINDS = frozenset(
    {
        "T65_FORMAL_PROVIDER",
        "T68_DOCUMENTATION",
        "T69_MANIFEST_COMPLETENESS",
        "T70_INDEPENDENT_HUMAN_REVIEW",
        "T71_REVISION_FREEZE",
        "T72_FINAL_ACCEPTANCE",
    }
)
FORBIDDEN_PUBLICATION_PREFIXES = (
    ".github/",
    "app/",
    "build/",
    "config/",
    "deploy/",
    "dist/",
    "frontend/",
    "migrations/",
    "src/",
    "scripts/",
    "tests/",
)
FORBIDDEN_PUBLICATION_FILES = frozenset(
    {
        "requirements.txt",
        "requirements.lock.txt",
        "requirements-linux.lock.txt",
        "requirements-windows.lock.txt",
        "dockerfile",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pyproject.toml",
        "yarn.lock",
    }
)
FORBIDDEN_PUBLICATION_PATH_FRAGMENTS = (
    "assignment-key",
    "assignment_key",
    "coordinator-only",
    "coordinator_only",
    "credential",
    "private-key",
    "private_key",
    "randomization-seed",
    "randomization_seed",
    "secret",
    "unblind",
    "unseal",
)
BLOCKED_STATUSES = frozenset({"NOT_RUN", "BLOCKED"})
SAFE_SENSITIVITY = "PUBLIC_SANITIZED"
SAFE_REDACTION_STATUSES = frozenset({"PASS", "NOT_REQUIRED"})
_CONNECTION_URI = re.compile(r"(?i)\b(?:postgres(?:ql)?|redis|rediss)://")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_API_KEY_ASSIGNMENT = re.compile(
    r"(?i)\b(?:OPENAI|DEEPSEEK|SILICONFLOW|ANTHROPIC)_API_KEY\s*[:=]\s*"
    r"(?![\"']?(?:redacted|none|null|your-api-key|\*+)[\"']?(?:\s|$))"
    r"[^\s,;}]+"
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(?:assignment[_ -]?key|credential|password|private[_ -]?key|"
    r"randomization[_ -]?seed|secret|unblind(?:ing)?[_ -]?(?:key|map)|"
    r"unseal[_ -]?token)\b\s*[:=]\s*(?![\"']?(?:redacted|none|null|"
    r"false|not[_ -]?stored|not[_ -]?recorded|\*+)[\"']?(?:\s|$))[^\s,;}]+"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}")
_JWT_TOKEN = re.compile(
    r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\b"
)
_MACHINE_PATHS = (
    re.compile(r"(?i)(?:^|[\s\"'`(])(?:[A-Z]:[\\/])Users[\\/]"),
    re.compile(r"(?i)(?:^|[\s\"'`(])/(?:home|Users|tmp|workspace)/"),
    re.compile(r"(?i)(?:^|[\s\"'`(])\\\\[^\\\s]+\\[^\\\s]+"),
)
_SENSITIVE_FIELDS = frozenset(
    {
        "api_key",
        "access_token",
        "authorization",
        "candidate_answer",
        "credential",
        "credentials",
        "database_url",
        "deepseek_api_key",
        "dsn",
        "openai_api_key",
        "password",
        "prompt",
        "messages",
        "private_key",
        "provider_payload",
        "raw_response",
        "resume_text",
        "job_description",
        "redis_url",
        "secret",
        "siliconflow_api_key",
    }
)
_SAFE_SECRET_MARKERS = frozenset(
    {"", "none", "not_recorded", "not_stored", "null", "redacted", "your-api-key"}
)


class PublicationVerificationError(RuntimeError):
    def __init__(self, codes: Sequence[str]) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("Interview Quality V1 publication verification failed")


@dataclass(frozen=True)
class TrustedGitRunner:
    executable: Path
    executable_sha256: str
    _executable_identity: tuple[object, ...] = dataclass_field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        resolved = self.executable.resolve(strict=True)
        if (
            not resolved.is_absolute()
            or not resolved.is_file()
            or _is_link_or_reparse(resolved)
            or not _is_native_executable(resolved)
        ):
            raise ValueError("trusted Git must be an absolute regular native executable")
        if sha256_bytes(resolved.read_bytes()) != self.executable_sha256:
            raise ValueError("trusted Git executable hash mismatch")
        object.__setattr__(self, "executable", resolved)
        identity = _native_executable_identity(resolved, self.executable_sha256)
        if identity is None:
            raise ValueError("trusted Git executable identity is invalid")
        object.__setattr__(self, "_executable_identity", identity)

    def run(
        self,
        root: Path,
        args: Sequence[str],
        *,
        check: bool = True,
        binary: bool = False,
    ) -> subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str]:
        before = _native_executable_identity(self.executable, self.executable_sha256)
        if before != self._executable_identity:
            raise PublicationVerificationError(["TRUSTED_GIT_EXECUTABLE_DRIFT"])
        if _resolve_git_metadata(root) is None:
            raise PublicationVerificationError(["GIT_METADATA_UNSAFE"])
        if not _local_git_config_safe(root):
            raise PublicationVerificationError(["GIT_LOCAL_CONFIG_UNSAFE"])
        environment = {
            key: value for key, value in os.environ.items() if key in _GIT_ENV_ALLOWLIST
        }
        environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "LANG": "C",
                "LC_ALL": "C",
            }
        )
        command = [
            str(self.executable),
            "--no-optional-locks",
            "--no-replace-objects",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            f"core.excludesFile={os.devnull}",
            "-c",
            f"core.attributesFile={os.devnull}",
            "-C",
            str(root.resolve()),
            *args,
        ]
        kwargs: dict[str, object] = {
            "cwd": self.executable.parent,
            "check": False,
            "capture_output": True,
            "env": environment,
            "timeout": 10,
        }
        if not binary:
            kwargs.update({"text": True, "encoding": "utf-8", "errors": "strict"})
        result = None
        try:
            result = subprocess.run(command, **kwargs)
        except UnicodeDecodeError as exc:
            raise PublicationVerificationError(["GIT_OUTPUT_NOT_UTF8"]) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise PublicationVerificationError(["GIT_COMMAND_FAILED"]) from exc
        finally:
            after = _native_executable_identity(
                self.executable, self.executable_sha256
            )
            if after != before:
                raise PublicationVerificationError(
                    ["TRUSTED_GIT_EXECUTABLE_DRIFT"]
                )
        assert result is not None
        if check and result.returncode != 0:
            raise PublicationVerificationError(["GIT_COMMAND_FAILED"])
        return result

    def assert_repository_safe(self, root: Path) -> None:
        top_level = _git(self, root, "rev-parse", "--show-toplevel", check=False)
        if Path(top_level).resolve(strict=False) != root.resolve(strict=False):
            raise PublicationVerificationError(["GIT_ROOT_MISMATCH"])
        status = _git(
            self,
            root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            check=False,
        )
        if status.strip():
            raise PublicationVerificationError(["GIT_WORKTREE_NOT_CLEAN"])
        flags = _git(self, root, "ls-files", "-v", check=False)
        if any(line and not line.startswith("H ") for line in flags.splitlines()):
            raise PublicationVerificationError(["GIT_INDEX_FLAGS_UNSAFE"])


def _production_git_runner() -> TrustedGitRunner | None:
    for candidate in _NATIVE_GIT_CANDIDATES:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        trusted_hashes = TRUSTED_GIT_EXECUTABLE_SHA256_BY_PATH.get(
            _git_candidate_key(resolved), frozenset()
        )
        for executable_sha256 in trusted_hashes:
            try:
                return TrustedGitRunner(resolved, executable_sha256)
            except (OSError, ValueError):
                continue
    return None


def _git_candidate_key(path: Path) -> str:
    rendered = str(path.resolve(strict=False)).replace("\\", "/")
    return rendered.casefold() if os.name == "nt" else rendered


def _is_native_executable(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
        if os.name != "nt" and not mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            return False
        with path.open("rb") as stream:
            header = stream.read(64)
            if header.startswith(b"\x7fELF"):
                return True
            if header[:4] in {
                b"\xfe\xed\xfa\xce",
                b"\xce\xfa\xed\xfe",
                b"\xfe\xed\xfa\xcf",
                b"\xcf\xfa\xed\xfe",
                b"\xca\xfe\xba\xbe",
                b"\xbe\xba\xfe\xca",
            }:
                return True
            if not header.startswith(b"MZ") or len(header) < 64:
                return False
            pe_offset = int.from_bytes(header[60:64], "little")
            if pe_offset < 64:
                return False
            stream.seek(pe_offset)
            pe_header = stream.read(26)
            if len(pe_header) != 26 or pe_header[:4] != b"PE\0\0":
                return False
            machine = int.from_bytes(pe_header[4:6], "little")
            section_count = int.from_bytes(pe_header[6:8], "little")
            optional_header_size = int.from_bytes(pe_header[20:22], "little")
            optional_magic = int.from_bytes(pe_header[24:26], "little")
            return (
                machine in {0x014C, 0x8664, 0xAA64}
                and section_count > 0
                and optional_header_size >= 0x60
                and optional_magic in {0x010B, 0x020B}
            )
    except OSError:
        return False


def _native_executable_identity(
    path: Path, expected_sha256: str
) -> tuple[object, ...] | None:
    try:
        if path.is_symlink():
            return None
        resolved = path.resolve(strict=True)
        if not resolved.is_file() or not _is_native_executable(resolved):
            return None
        metadata = resolved.stat()
        digest = sha256_bytes(resolved.read_bytes())
    except OSError:
        return None
    if digest != expected_sha256:
        return None
    return (
        _git_candidate_key(resolved),
        digest,
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _resolve_git_metadata(root: Path) -> tuple[Path, Path] | None:
    marker = root / ".git"
    try:
        if _is_link_or_reparse(root) or not root.resolve(strict=True).is_dir():
            return None
        if _is_link_or_reparse(marker):
            return None
        if marker.is_dir():
            git_directory = marker.resolve(strict=True)
        elif marker.is_file():
            raw = marker.read_bytes()
            if len(raw) > 4096:
                return None
            text_value = raw.decode("utf-8").strip()
            if "\x00" in text_value or "\n" in text_value or not text_value.startswith("gitdir: "):
                return None
            configured = Path(text_value[8:])
            if not configured.is_absolute():
                configured = root / configured
            if _is_link_or_reparse(configured):
                return None
            git_directory = configured.resolve(strict=True)
        else:
            return None
        if not git_directory.is_dir():
            return None
        common_marker = git_directory / "commondir"
        if common_marker.exists() or common_marker.is_symlink():
            if _is_link_or_reparse(common_marker) or not common_marker.is_file():
                return None
            raw_common = common_marker.read_bytes()
            if len(raw_common) > 4096:
                return None
            common_text = raw_common.decode("utf-8").strip()
            if not common_text or "\x00" in common_text or "\n" in common_text:
                return None
            configured_common = Path(common_text)
            if not configured_common.is_absolute():
                configured_common = git_directory / configured_common
            if _is_link_or_reparse(configured_common):
                return None
            common_directory = configured_common.resolve(strict=True)
        else:
            common_directory = git_directory
        if not common_directory.is_dir():
            return None
        for directory in {git_directory, common_directory}:
            alternates = directory / "objects" / "info" / "alternates"
            if alternates.exists() or alternates.is_symlink():
                return None
            config_worktree = directory / "config.worktree"
            if config_worktree.exists() or config_worktree.is_symlink():
                return None
        config = common_directory / "config"
        if _is_link_or_reparse(config) or not config.is_file():
            return None
        return git_directory, common_directory
    except (OSError, UnicodeDecodeError):
        return None


def _local_git_config_safe(root: Path) -> bool:
    metadata = _resolve_git_metadata(root)
    if metadata is None:
        return False
    git_directory, common_directory = metadata
    config_paths = [common_directory / "config"]
    worktree_config = git_directory / "config.worktree"
    if worktree_config.exists():
        config_paths.append(worktree_config)
    section = ""
    try:
        for config_path in config_paths:
            text_value = config_path.read_text(encoding="utf-8")
            if len(text_value.encode("utf-8")) > 1_000_000:
                return False
            for raw_line in text_value.splitlines():
                line = raw_line.strip()
                if not line or line.startswith(("#", ";")):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1].strip().casefold()
                    continue
                if "=" not in line:
                    return False
                key = line.split("=", 1)[0].strip().casefold()
                section_base = section.split(" ", 1)[0]
                dangerous = (
                    (section_base in {"include", "includeif"} and key == "path")
                    or (
                        section_base == "core"
                        and key
                        in {"worktree", "fsmonitor", "hookspath", "excludesfile", "attributesfile"}
                    )
                    or (section_base == "extensions" and key == "worktreeconfig")
                    or (
                        section_base == "filter"
                        and key in {"clean", "smudge", "process", "required"}
                    )
                    or (section_base == "diff" and key in {"command", "textconv"})
                )
                if dangerous:
                    return False
    except (OSError, UnicodeDecodeError):
        return False
    return True


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_sha256(value: object) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(rendered)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def public_key_fingerprint(raw_public_key: bytes) -> str:
    return sha256_bytes(raw_public_key)


def _verify_detached_signature(
    block: object,
    *,
    expected_payload: Mapping[str, object],
    trusted_public_keys: Mapping[str, bytes],
    prefix: str,
) -> list[str]:
    if not isinstance(block, Mapping):
        return [f"{prefix}_REQUIRED"]
    if block.get("signed_payload") != expected_payload:
        return [f"{prefix}_PAYLOAD_MISMATCH"]
    fingerprint = block.get("signer_fingerprint")
    if not isinstance(fingerprint, str) or fingerprint not in trusted_public_keys:
        return [f"{prefix}_SIGNER_UNTRUSTED"]
    raw_key = trusted_public_keys[fingerprint]
    if public_key_fingerprint(raw_key) != fingerprint:
        return [f"{prefix}_TRUST_STORE_INVALID"]
    signature_value = block.get("signature_base64")
    if not isinstance(signature_value, str):
        return [f"{prefix}_SIGNATURE_INVALID"]
    try:
        signature = base64.b64decode(signature_value, validate=True)
        Ed25519PublicKey.from_public_bytes(raw_key).verify(
            signature, canonical_bytes(expected_payload)
        )
    except (ValueError, binascii.Error, InvalidSignature):
        return [f"{prefix}_SIGNATURE_INVALID"]
    return []


def load_json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PublicationVerificationError(["JSON_OBJECT_REQUIRED"])
    return value


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _candidate_path(root: Path, relative: str) -> Path | None:
    if not isinstance(relative, str) or not relative:
        return None
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in relative
        or ":" in pure.parts[0]
    ):
        return None
    root_resolved = root.resolve()
    candidate = root.joinpath(*pure.parts)
    current = root_resolved
    for part in pure.parts:
        current = current / part
        if _is_link_or_reparse(current):
            return None
    try:
        candidate.resolve(strict=False).relative_to(root_resolved)
    except ValueError:
        return None
    return candidate


def safe_repo_file(root: Path, relative: str) -> Path | None:
    candidate = _candidate_path(root, relative)
    if candidate is None:
        return None
    try:
        metadata = candidate.lstat()
    except OSError:
        return None
    if _is_link_or_reparse(candidate) or not stat.S_ISREG(metadata.st_mode):
        return None
    return candidate


def file_identity(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {"bytes": len(content), "sha256": sha256_bytes(content)}


def evidence_bundle_sha256(entries: object) -> str:
    return canonical_sha256(entries)


def _scan_public_text(content: bytes) -> list[str]:
    try:
        rendered = content.decode("utf-8")
    except UnicodeDecodeError:
        return ["PUBLICATION_TEXT_NOT_UTF8"]
    codes: list[str] = []
    if _CONNECTION_URI.search(rendered):
        codes.append("CONNECTION_SECRET_PRESENT")
    if _PRIVATE_KEY.search(rendered):
        codes.append("PRIVATE_KEY_PRESENT")
    if _API_KEY_ASSIGNMENT.search(rendered):
        codes.append("API_KEY_VALUE_PRESENT")
    if _SENSITIVE_ASSIGNMENT.search(rendered):
        codes.append("SENSITIVE_VALUE_PRESENT")
    if _BEARER_TOKEN.search(rendered):
        codes.append("BEARER_TOKEN_PRESENT")
    if _JWT_TOKEN.search(rendered):
        codes.append("JWT_TOKEN_PRESENT")
    if any(pattern.search(rendered) for pattern in _MACHINE_PATHS):
        codes.append("MACHINE_SPECIFIC_PATH_PRESENT")
    try:
        structured = json.loads(rendered)
    except json.JSONDecodeError:
        structured = None
    if structured is not None and _has_sensitive_value(structured):
        codes.append("SENSITIVE_FIELD_VALUE_PRESENT")
    return codes


def _has_sensitive_value(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in _SENSITIVE_FIELDS:
                if item is None or item is False:
                    continue
                if isinstance(item, str) and item.strip().casefold() in _SAFE_SECRET_MARKERS:
                    continue
                return True
            if _has_sensitive_value(item):
                return True
    elif isinstance(value, list):
        return any(_has_sensitive_value(item) for item in value)
    return False


def _validate_null_reason(value: object, *, path: str = "$") -> list[str]:
    codes: list[str] = []
    if isinstance(value, Mapping):
        status = value.get("status")
        if status in BLOCKED_STATUSES:
            if value.get("value", None) is not None:
                codes.append(f"NOT_RUN_VALUE_MUST_BE_NULL:{path}")
            reason = value.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                codes.append(f"NOT_RUN_REASON_REQUIRED:{path}")
        for key, item in value.items():
            codes.extend(_validate_null_reason(item, path=f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            codes.extend(_validate_null_reason(item, path=f"{path}[{index}]"))
    return codes


def validate_evidence_index(
    index: Mapping[str, object],
    *,
    root: Path,
    index_path: str,
    manifest_path: str,
    expected_implementation_sha: str | None = None,
    expected_implementation_tree: str | None = None,
) -> dict[str, object]:
    codes: list[str] = []
    if index.get("schema_version") != INDEX_SCHEMA:
        codes.append("INDEX_SCHEMA_INVALID")
    if index.get("hash_algorithm") != "sha256-raw-file-bytes-v1":
        codes.append("INDEX_HASH_ALGORITHM_INVALID")
    index_implementation_sha = str(index.get("implementation_sha", ""))
    index_implementation_tree = str(index.get("implementation_tree", ""))
    if SHA40.fullmatch(index_implementation_sha) is None:
        codes.append("INDEX_IMPLEMENTATION_SHA_INVALID")
    if SHA40.fullmatch(index_implementation_tree) is None:
        codes.append("INDEX_IMPLEMENTATION_TREE_INVALID")
    if (
        expected_implementation_sha is not None
        and index_implementation_sha != expected_implementation_sha
    ):
        codes.append("INDEX_IMPLEMENTATION_SHA_MISMATCH")
    if (
        expected_implementation_tree is not None
        and index_implementation_tree != expected_implementation_tree
    ):
        codes.append("INDEX_IMPLEMENTATION_TREE_MISMATCH")
    entries_value = index.get("entries")
    entries = entries_value if isinstance(entries_value, list) else []
    if not isinstance(entries_value, list):
        codes.append("INDEX_ENTRIES_INVALID")
    if not entries:
        codes.append("INDEX_ENTRIES_EMPTY")
    if index.get("entry_count") != len(entries):
        codes.append("INDEX_ENTRY_COUNT_MISMATCH")

    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    seen_kinds: set[str] = set()
    forbidden_indexed_paths = {index_path.casefold(), manifest_path.casefold()}
    verified = 0
    for item in entries:
        if not isinstance(item, Mapping):
            codes.append("INDEX_ENTRY_INVALID")
            continue
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            codes.append("EVIDENCE_ID_INVALID")
        elif evidence_id in seen_ids:
            codes.append("EVIDENCE_ID_DUPLICATE")
        else:
            seen_ids.add(evidence_id)

        kind = item.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            codes.append("EVIDENCE_KIND_INVALID")
        else:
            seen_kinds.add(kind)

        relative = item.get("path")
        if not isinstance(relative, str):
            codes.append("EVIDENCE_PATH_INVALID")
            continue
        folded = relative.casefold()
        if folded in seen_paths:
            codes.append("EVIDENCE_PATH_DUPLICATE")
        else:
            seen_paths.add(folded)
        if folded in forbidden_indexed_paths:
            codes.append("INDEX_SELF_OR_MANIFEST_REFERENCE")
        if "coordinator-only" in folded or "coordinator_only" in folded:
            codes.append("COORDINATOR_ONLY_REFERENCE")
        if not folded.startswith(PUBLICATION_EVIDENCE_PREFIX.casefold()):
            codes.append("EVIDENCE_PATH_OUTSIDE_PUBLICATION_DIRECTORY")

        path = safe_repo_file(root, relative)
        if path is None:
            codes.append("EVIDENCE_PATH_UNSAFE_OR_MISSING")
            continue
        content = path.read_bytes()
        identity = {"bytes": len(content), "sha256": sha256_bytes(content)}
        if item.get("bytes") != identity["bytes"]:
            codes.append("EVIDENCE_SIZE_MISMATCH")
        if item.get("sha256") != identity["sha256"]:
            codes.append("EVIDENCE_HASH_MISMATCH")
        if item.get("sensitivity") != SAFE_SENSITIVITY:
            codes.append("EVIDENCE_SENSITIVITY_INVALID")
        if item.get("publishable") is not True:
            codes.append("EVIDENCE_NOT_PUBLISHABLE")
        if item.get("contains_secrets") is not False:
            codes.append("EVIDENCE_SECRET_FLAG_INVALID")
        if item.get("contains_real_candidate_data") is not False:
            codes.append("REAL_CANDIDATE_DATA_PRESENT")
        if item.get("redaction_status") not in SAFE_REDACTION_STATUSES:
            codes.append("EVIDENCE_REDACTION_INVALID")
        if item.get("bound_revision") != index_implementation_sha:
            codes.append("EVIDENCE_IMPLEMENTATION_SHA_MISMATCH")
        if item.get("bound_tree") != index_implementation_tree:
            codes.append("EVIDENCE_IMPLEMENTATION_TREE_MISMATCH")
        if kind in REQUIRED_EVIDENCE_KINDS and item.get("status") != "PASS":
            codes.append(f"REQUIRED_EVIDENCE_NOT_PASS:{kind}")
        try:
            document = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            document = None
        if not isinstance(document, Mapping):
            codes.append("EVIDENCE_DOCUMENT_INVALID")
        else:
            if document.get("evidence_id") != evidence_id:
                codes.append("EVIDENCE_DOCUMENT_ID_MISMATCH")
            if document.get("kind") != kind:
                codes.append("EVIDENCE_DOCUMENT_KIND_MISMATCH")
            if document.get("status") != "PASS":
                codes.append("EVIDENCE_DOCUMENT_STATUS_NOT_PASS")
            if document.get("bound_revision") != index_implementation_sha:
                codes.append("EVIDENCE_DOCUMENT_REVISION_MISMATCH")
            if document.get("bound_tree") != index_implementation_tree:
                codes.append("EVIDENCE_DOCUMENT_TREE_MISMATCH")
        codes.extend(_scan_public_text(content))
        codes.extend(_validate_null_reason(item, path=f"$.entries[{evidence_id!r}]"))
        verified += 1

    missing_kinds = sorted(REQUIRED_EVIDENCE_KINDS - seen_kinds)
    if missing_kinds:
        codes.append("REQUIRED_EVIDENCE_KINDS_MISSING:" + ",".join(missing_kinds))
    evidence_root = root / PUBLICATION_EVIDENCE_PREFIX.rstrip("/")
    actual_paths: set[str] = set()
    if evidence_root.is_dir() and not _is_link_or_reparse(evidence_root):
        for candidate in evidence_root.rglob("*"):
            if candidate.is_file() and not _is_link_or_reparse(candidate):
                actual_paths.add(candidate.relative_to(root).as_posix().casefold())
    indexed_evidence_paths = {
        path for path in seen_paths if path.startswith(PUBLICATION_EVIDENCE_PREFIX.casefold())
    }
    if actual_paths != indexed_evidence_paths:
        codes.append("EVIDENCE_DIRECTORY_INDEX_MISMATCH")

    if index.get("bundle_sha256") != evidence_bundle_sha256(entries):
        codes.append("INDEX_BUNDLE_HASH_MISMATCH")
    codes.extend(_validate_null_reason(index))
    codes.extend(_scan_public_text(json.dumps(index, ensure_ascii=False).encode("utf-8")))
    if codes:
        raise PublicationVerificationError(codes)
    return {"entries_verified": verified, "bundle_sha256_match": True}


def _git(
    runner: TrustedGitRunner,
    root: Path,
    *args: str,
    check: bool = True,
) -> str:
    result = runner.run(root, args, check=check)
    assert isinstance(result.stdout, str)
    return result.stdout.strip()


def _git_blob(
    runner: TrustedGitRunner, root: Path, revision: str, relative: str
) -> bytes | None:
    result = runner.run(
        root,
        ("cat-file", "blob", f"{revision}:{relative}"),
        check=False,
        binary=True,
    )
    assert isinstance(result.stdout, bytes)
    return result.stdout if result.returncode == 0 else None


def _resolve_commit(
    runner: TrustedGitRunner, root: Path, revision_or_ref: object
) -> str | None:
    if not isinstance(revision_or_ref, str) or not revision_or_ref:
        return None
    value = _git(
        runner,
        root,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{revision_or_ref}^{{commit}}",
        check=False,
    )
    return value if SHA40.fullmatch(value) else None


def _commit_tree(runner: TrustedGitRunner, root: Path, revision: str) -> str | None:
    value = _git(
        runner, root, "show", "-s", "--format=%T", revision, "--", check=False
    )
    return value if SHA40.fullmatch(value) else None


def _commit_parents(
    runner: TrustedGitRunner, root: Path, revision: str
) -> tuple[str, ...]:
    value = _git(
        runner, root, "show", "-s", "--format=%P", revision, "--", check=False
    )
    return tuple(part for part in value.split() if SHA40.fullmatch(part))


def _changed_paths(
    runner: TrustedGitRunner, root: Path, start: str, end: str
) -> set[str]:
    result = runner.run(
        root,
        ("diff", "--name-only", "--no-renames", "-z", start, end, "--"),
        binary=True,
    )
    assert isinstance(result.stdout, bytes)
    try:
        paths = result.stdout.decode("utf-8").split("\0")
    except UnicodeDecodeError as exc:
        raise PublicationVerificationError(["GIT_PATH_NOT_UTF8"]) from exc
    if not paths or paths[-1] != "":
        raise PublicationVerificationError(["GIT_NUL_PATH_OUTPUT_INVALID"])
    values = paths[:-1]
    if any(not path or "\0" in path for path in values):
        raise PublicationVerificationError(["GIT_NUL_PATH_OUTPUT_INVALID"])
    return set(values)


def _git_regular_file_blob(
    runner: TrustedGitRunner, root: Path, revision: str, relative: str
) -> bytes | None:
    result = runner.run(
        root, ("ls-tree", "-z", revision, "--", relative), check=False, binary=True
    )
    assert isinstance(result.stdout, bytes)
    if result.returncode != 0 or not result.stdout:
        return None
    records = result.stdout.split(b"\0")
    if len(records) != 2 or records[-1] != b"":
        return None
    metadata, separator, listed_path = records[0].partition(b"\t")
    try:
        fields = metadata.decode("ascii").split()
        decoded_path = listed_path.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if (
        not separator
        or decoded_path != relative
        or len(fields) != 3
        or fields[0] not in {"100644", "100755"}
        or fields[1] != "blob"
    ):
        return None
    return _git_blob(runner, root, revision, relative)


def _publication_path_denied(path: str) -> bool:
    folded = path.casefold()
    basename = PurePosixPath(folded).name
    return (
        folded.startswith(FORBIDDEN_PUBLICATION_PREFIXES)
        or basename in FORBIDDEN_PUBLICATION_FILES
        or PurePosixPath(folded).suffix in {".bat", ".cmd", ".ps1", ".sh"}
        or any(fragment in folded for fragment in FORBIDDEN_PUBLICATION_PATH_FRAGMENTS)
    )


def _publication_path_allowed(
    path: str,
    *,
    allowed_files: frozenset[str],
    allowed_prefixes: tuple[str, ...],
    indexed_files: frozenset[str] = frozenset(),
) -> bool:
    if (
        _publication_path_denied(path)
    ):
        return False
    if path in allowed_files:
        return True
    if path.casefold() not in indexed_files:
        return False
    return bool(allowed_prefixes) and path.casefold().startswith(
        tuple(prefix.casefold() for prefix in allowed_prefixes)
    )


def _validate_acceptance(manifest: Mapping[str, object]) -> list[str]:
    acceptance = manifest.get("acceptance")
    if not isinstance(acceptance, Mapping):
        return ["ACCEPTANCE_REQUIRED"]
    codes: list[str] = []
    for field, code in (
        ("engineering_status", "ENGINEERING_NOT_PASS"),
        ("quality_status", "QUALITY_NOT_PASS"),
        ("final_acceptance", "FINAL_ACCEPTANCE_NOT_PASS"),
        ("t65_formal_provider_status", "T65_FORMAL_PROVIDER_NOT_PASS"),
    ):
        if acceptance.get(field) != "PASS":
            codes.append(code)
    review = acceptance.get("independent_human_review")
    if not isinstance(review, Mapping):
        codes.append("INDEPENDENT_HUMAN_REVIEW_REQUIRED")
    else:
        if review.get("status") != "PASS" or review.get("reviewer_independent") is not True:
            codes.append("INDEPENDENT_HUMAN_REVIEW_NOT_PASS")
        if not isinstance(review.get("evidence_id"), str) or not review.get("evidence_id"):
            codes.append("INDEPENDENT_HUMAN_REVIEW_EVIDENCE_REQUIRED")
    tests = acceptance.get("required_tests")
    if not isinstance(tests, Mapping):
        codes.append("REQUIRED_TESTS_REQUIRED")
    elif (
        tests.get("status") != "PASS"
        or not isinstance(tests.get("total"), int)
        or tests.get("total", 0) <= 0
        or tests.get("failed") != 0
    ):
        codes.append("REQUIRED_TESTS_NOT_PASS")
    if acceptance.get("blocking_skips") != 0:
        codes.append("BLOCKING_SKIPS_NONZERO")
    unresolved = acceptance.get("unresolved_findings")
    if not isinstance(unresolved, Mapping):
        codes.append("UNRESOLVED_FINDINGS_REQUIRED")
    else:
        if unresolved.get("p0") != 0:
            codes.append("UNRESOLVED_P0_NONZERO")
        if unresolved.get("p1") != 0:
            codes.append("UNRESOLVED_P1_NONZERO")
    return codes


def _revision_block(
    value: object,
    *,
    name: str,
    require_sha_tree: bool,
) -> tuple[Mapping[str, object] | None, list[str]]:
    codes: list[str] = []
    if not isinstance(value, Mapping):
        return None, [f"{name.upper()}_REVISION_BLOCK_INVALID"]
    if require_sha_tree:
        if SHA40.fullmatch(str(value.get("sha", ""))) is None:
            codes.append(f"{name.upper()}_SHA_INVALID")
        if SHA40.fullmatch(str(value.get("tree", ""))) is None:
            codes.append(f"{name.upper()}_TREE_INVALID")
    return value, codes


def verify_publication(
    manifest: Mapping[str, object],
    index: Mapping[str, object],
    *,
    root: Path,
    manifest_path: str,
    index_path: str,
    allowed_publication_files: frozenset[str] = DEFAULT_ALLOWED_PUBLICATION_FILES,
    allowed_publication_prefixes: tuple[str, ...] = DEFAULT_ALLOWED_PUBLICATION_PREFIXES,
    trusted_public_keys: Mapping[str, bytes] | None = None,
    final_ref_receipt: Mapping[str, object] | None = None,
    git_runner: TrustedGitRunner | None = None,
) -> dict[str, object]:
    trusted_public_keys = trusted_public_keys or TRUSTED_PUBLICATION_AUTHORITY_PUBLIC_KEYS
    git_runner = git_runner or _production_git_runner()
    if git_runner is None:
        raise PublicationVerificationError(["TRUSTED_GIT_UNAVAILABLE"])
    codes: list[str] = []
    try:
        git_runner.assert_repository_safe(root)
    except PublicationVerificationError as exc:
        if exc.codes == ("GIT_WORKTREE_NOT_CLEAN",):
            codes.extend(exc.codes)
        else:
            raise
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        codes.append("MANIFEST_SCHEMA_INVALID")
    codes.extend(_validate_acceptance(manifest))
    revisions = manifest.get("revisions")
    if not isinstance(revisions, Mapping):
        raise PublicationVerificationError(["REVISIONS_INVALID"])
    implementation, found = _revision_block(
        revisions.get("implementation"), name="implementation", require_sha_tree=True
    )
    codes.extend(found)
    publication, found = _revision_block(
        revisions.get("publication"), name="publication", require_sha_tree=True
    )
    codes.extend(found)
    final, found = _revision_block(
        revisions.get("final"), name="final", require_sha_tree=False
    )
    codes.extend(found)
    if implementation is None or publication is None or final is None:
        raise PublicationVerificationError(codes)

    implementation_sha = str(implementation.get("sha", ""))
    publication_sha = str(publication.get("sha", ""))
    implementation_tree = str(implementation.get("tree", ""))
    publication_tree = str(publication.get("tree", ""))
    publication_ref = publication.get("ref")
    final_ref = final.get("ref")
    if not isinstance(publication_ref, str) or SAFE_REF.fullmatch(publication_ref) is None:
        codes.append("PUBLICATION_REF_INVALID")
    if not isinstance(final_ref, str) or SAFE_REF.fullmatch(final_ref) is None:
        codes.append("FINAL_REF_INVALID")
    resolved_implementation = _resolve_commit(git_runner, root, implementation_sha)
    resolved_publication = _resolve_commit(git_runner, root, publication_ref)
    resolved_final = _resolve_commit(git_runner, root, final_ref)
    if resolved_implementation != implementation_sha:
        codes.append("IMPLEMENTATION_REVISION_MISMATCH")
    if resolved_publication != publication_sha:
        codes.append("PUBLICATION_REVISION_MISMATCH")
    if resolved_final is None:
        codes.append("FINAL_REF_UNRESOLVED")
    if resolved_implementation and _commit_tree(git_runner, root, resolved_implementation) != implementation_tree:
        codes.append("IMPLEMENTATION_TREE_MISMATCH")
    if resolved_publication and _commit_tree(git_runner, root, resolved_publication) != publication_tree:
        codes.append("PUBLICATION_TREE_MISMATCH")
    if final.get("self_sha_recorded") is not False:
        codes.append("FINAL_SELF_HASH_FLAG_INVALID")
    if final.get("sha") is not None or final.get("tree") is not None:
        codes.append("FINAL_SELF_HASH_FORBIDDEN")
    if len({implementation_sha, publication_sha, resolved_final}) != 3:
        codes.append("I_P_F_MUST_BE_DISTINCT")
    if resolved_implementation and resolved_publication:
        if _commit_parents(git_runner, root, resolved_publication) != (resolved_implementation,):
            codes.append("PUBLICATION_PARENT_NOT_IMPLEMENTATION")
    if resolved_publication and resolved_final:
        if _commit_parents(git_runner, root, resolved_final) != (resolved_publication,):
            codes.append("FINAL_PARENT_NOT_PUBLICATION")

    acceptance = manifest.get("acceptance")
    review = acceptance.get("independent_human_review") if isinstance(acceptance, Mapping) else None
    if isinstance(review, Mapping):
        review_payload = {
            "schema_version": "interview-quality-v1-independent-review-signature-v1",
            "implementation_sha": implementation_sha,
            "implementation_tree": implementation_tree,
            "evidence_id": review.get("evidence_id"),
            "reviewer_independent": True,
            "status": "PASS",
        }
        codes.extend(
            _verify_detached_signature(
                review,
                expected_payload=review_payload,
                trusted_public_keys=trusted_public_keys,
                prefix="INDEPENDENT_HUMAN_REVIEW",
            )
        )
    final_tree = _commit_tree(git_runner, root, resolved_final) if resolved_final else None
    receipt_payload = {
        "schema_version": "interview-quality-v1-final-ref-receipt-v1",
        "implementation_sha": implementation_sha,
        "implementation_tree": implementation_tree,
        "publication_sha": publication_sha,
        "publication_tree": publication_tree,
        "final_ref": final_ref,
        "final_sha": resolved_final,
        "final_tree": final_tree,
        "status": "PASS",
    }
    codes.extend(
        _verify_detached_signature(
            final_ref_receipt,
            expected_payload=receipt_payload,
            trusted_public_keys=trusted_public_keys,
            prefix="EXTERNAL_FINAL_REF_RECEIPT",
        )
    )

    entries_value = index.get("entries")
    evidence_by_id = {
        str(item.get("evidence_id")): item.get("kind")
        for item in (entries_value if isinstance(entries_value, list) else [])
        if isinstance(item, Mapping) and isinstance(item.get("evidence_id"), str)
    }
    review = acceptance.get("independent_human_review") if isinstance(acceptance, Mapping) else None
    if isinstance(review, Mapping) and evidence_by_id.get(str(review.get("evidence_id"))) != "T70_INDEPENDENT_HUMAN_REVIEW":
        codes.append("INDEPENDENT_HUMAN_REVIEW_EVIDENCE_MISMATCH")
    indexed_publication_files = frozenset(
        str(item["path"]).casefold()
        for item in (entries_value if isinstance(entries_value, list) else [])
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    )
    fixed_publication_files = frozenset(
        {*allowed_publication_files, manifest_path, index_path}
    )
    for start, end, label in (
        (resolved_implementation, resolved_publication, "PUBLICATION"),
        (resolved_publication, resolved_final, "FINAL"),
    ):
        if not start or not end:
            continue
        changed = _changed_paths(git_runner, root, start, end)
        if not changed:
            codes.append(f"{label}_DIFF_EMPTY")
        if label == "FINAL":
            allowed = changed <= {manifest_path}
        else:
            allowed = all(
                _publication_path_allowed(
                    path,
                    allowed_files=fixed_publication_files,
                    allowed_prefixes=allowed_publication_prefixes,
                    indexed_files=indexed_publication_files,
                )
                for path in changed
            )
        if not allowed:
            codes.append(f"{label}_DIFF_NOT_ALLOWLISTED")
        for relative in changed:
            folded = relative.casefold()
            is_fixed_control = relative in fixed_publication_files
            is_indexed_evidence = folded in indexed_publication_files
            if _publication_path_denied(relative):
                codes.append(f"{label}_CHANGED_PATH_DENIED")
            if not is_fixed_control and not is_indexed_evidence:
                codes.append(f"{label}_CHANGED_FILE_NOT_INDEXED")
            current = safe_repo_file(root, relative)
            frozen = _git_regular_file_blob(git_runner, root, end, relative)
            if current is None:
                codes.append(f"{label}_CHANGED_FILE_UNSAFE_OR_MISSING")
            if frozen is None:
                codes.append(f"{label}_CHANGED_FILE_NOT_REGULAR")
                continue
            codes.extend(_scan_public_text(frozen))

    index_ref = manifest.get("evidence_index")
    index_file = safe_repo_file(root, index_path)
    if not isinstance(index_ref, Mapping) or index_file is None:
        codes.append("MANIFEST_INDEX_REFERENCE_INVALID")
    else:
        identity = file_identity(index_file)
        if index_ref.get("path") != index_path:
            codes.append("MANIFEST_INDEX_PATH_MISMATCH")
        if index_ref.get("bytes") != identity["bytes"]:
            codes.append("MANIFEST_INDEX_SIZE_MISMATCH")
        if index_ref.get("sha256") != identity["sha256"]:
            codes.append("MANIFEST_INDEX_HASH_MISMATCH")

    try:
        index_result = validate_evidence_index(
            index,
            root=root,
            index_path=index_path,
            manifest_path=manifest_path,
            expected_implementation_sha=implementation_sha,
            expected_implementation_tree=implementation_tree,
        )
    except PublicationVerificationError as exc:
        codes.extend(exc.codes)
        index_result = {}
    if resolved_final:
        for relative, code in (
            (manifest_path, "FINAL_MANIFEST_BLOB_MISMATCH"),
            (index_path, "FINAL_INDEX_BLOB_MISMATCH"),
        ):
            current = safe_repo_file(root, relative)
            frozen = _git_blob(git_runner, root, resolved_final, relative)
            if current is None or frozen is None or current.read_bytes() != frozen:
                codes.append(code)
        if isinstance(entries_value, list):
            for item in entries_value:
                if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
                    continue
                relative = str(item["path"])
                current = safe_repo_file(root, relative)
                frozen = _git_blob(git_runner, root, resolved_final, relative)
                if current is None or frozen is None or current.read_bytes() != frozen:
                    codes.append("FINAL_EVIDENCE_BLOB_MISMATCH")
    if resolved_publication and resolved_final:
        for relative in (index_path, *sorted(indexed_publication_files)):
            publication_blob = _git_blob(git_runner, root, resolved_publication, relative)
            final_blob = _git_blob(git_runner, root, resolved_final, relative)
            if publication_blob is None or publication_blob != final_blob:
                codes.append("PUBLICATION_ARTIFACT_CHANGED_AFTER_FREEZE")
    codes.extend(_validate_null_reason(manifest))
    codes.extend(_scan_public_text(json.dumps(manifest, ensure_ascii=False).encode("utf-8")))
    if manifest.get("publication_commit_self_hash_recorded") is not False:
        codes.append("PUBLICATION_SELF_HASH_FLAG_INVALID")
    if codes:
        raise PublicationVerificationError(codes)
    return {
        "status": "PASS",
        "implementation_sha": implementation_sha,
        "publication_sha": publication_sha,
        "final_sha": resolved_final,
        "entries_verified": index_result["entries_verified"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify an offline Interview Quality V1 publication bundle."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", default="docs/interview-quality-v1-execution-manifest.json")
    parser.add_argument("--index", default="docs/interview-quality-v1-evidence-index.json")
    parser.add_argument("--final-ref-receipt", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    manifest_file = safe_repo_file(root, args.manifest)
    index_file = safe_repo_file(root, args.index)
    if manifest_file is None or index_file is None:
        print("INTERVIEW_QUALITY_V1_PUBLICATION=BLOCKED")
        print("GATE=PUBLICATION_INPUT_PATH_INVALID")
        return 1
    try:
        final_ref_receipt = (
            load_json_object(args.final_ref_receipt)
            if args.final_ref_receipt is not None
            else None
        )
    except (OSError, ValueError, binascii.Error, json.JSONDecodeError):
        print("INTERVIEW_QUALITY_V1_PUBLICATION=BLOCKED")
        print("GATE=PUBLICATION_TRUST_INPUT_INVALID")
        return 1
    try:
        result = verify_publication(
            load_json_object(manifest_file),
            load_json_object(index_file),
            root=root,
            manifest_path=args.manifest,
            index_path=args.index,
            trusted_public_keys=TRUSTED_PUBLICATION_AUTHORITY_PUBLIC_KEYS,
            final_ref_receipt=final_ref_receipt,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, PublicationVerificationError) as exc:
        print("INTERVIEW_QUALITY_V1_PUBLICATION=BLOCKED")
        codes = getattr(exc, "codes", ("PUBLICATION_JSON_INVALID",))
        for code in codes:
            print(f"GATE={code}")
        return 1
    print("INTERVIEW_QUALITY_V1_PUBLICATION=PASS")
    print(f"IMPLEMENTATION_SHA={result['implementation_sha']}")
    print(f"PUBLICATION_SHA={result['publication_sha']}")
    print(f"FINAL_SHA={result['final_sha']}")
    print(f"EVIDENCE_ENTRIES={result['entries_verified']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
