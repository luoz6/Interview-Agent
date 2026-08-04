from __future__ import print_function

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys


PYTHON_VERSION_UNSUPPORTED = "PYTHON_VERSION_UNSUPPORTED"
PYTHON_ENVIRONMENT_MISMATCH = "PYTHON_ENVIRONMENT_MISMATCH"
NODE_VERSION_UNSUPPORTED = "NODE_VERSION_UNSUPPORTED"


class ReproducibilityPreflightError(RuntimeError):
    def __init__(self, gate_code):
        self.gate_code = gate_code
        super().__init__(gate_code)


def validate_python_environment(
    *,
    version_info,
    executable,
    prefix,
    base_prefix,
    virtual_env=None,
    require_venv=False,
):
    if tuple(version_info[:2]) != (3, 11):
        raise ReproducibilityPreflightError(PYTHON_VERSION_UNSUPPORTED)

    prefix_path = Path(prefix).resolve()
    executable_path = Path(executable).resolve()
    in_venv = Path(base_prefix).resolve() != prefix_path
    try:
        executable_path.relative_to(prefix_path)
    except ValueError as exc:
        raise ReproducibilityPreflightError(
            PYTHON_ENVIRONMENT_MISMATCH
        ) from exc
    if virtual_env:
        if os.path.normcase(str(Path(virtual_env).resolve())) != os.path.normcase(
            str(prefix_path)
        ):
            raise ReproducibilityPreflightError(
                PYTHON_ENVIRONMENT_MISMATCH
            )
    if require_venv and not in_venv:
        raise ReproducibilityPreflightError(PYTHON_ENVIRONMENT_MISMATCH)
    return {
        "python_major_minor": "3.11",
        "python_executable_source": "virtualenv" if in_venv else "system",
    }


def validate_node_version(value, *, allow_node20=False):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
    allowed = {22, 20} if allow_node20 else {22}
    if match is None or int(match.group(1)) not in allowed:
        raise ReproducibilityPreflightError(NODE_VERSION_UNSUPPORTED)
    return ".".join(match.groups())


def dependency_inventory_sha256(distributions=None):
    installed = (
        importlib.metadata.distributions()
        if distributions is None
        else distributions
    )
    inventory = sorted(
        (
            str(item.metadata.get("Name", "")).casefold().replace("_", "-"),
            str(item.version),
        )
        for item in installed
        if item.metadata.get("Name")
    )
    payload = json.dumps(
        inventory,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _node_version():
    try:
        return subprocess.check_output(
            ["node", "--version"],
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReproducibilityPreflightError(
            NODE_VERSION_UNSUPPORTED
        ) from exc


def build_preflight_result(*, require_venv=False, python_only=False):
    result = validate_python_environment(
        version_info=sys.version_info,
        executable=sys.executable,
        prefix=sys.prefix,
        base_prefix=sys.base_prefix,
        virtual_env=os.getenv("VIRTUAL_ENV"),
        require_venv=require_venv,
    )
    result.update(
        {
            "os_family": platform.system(),
            "os_version": platform.release(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "dependency_inventory_sha256": dependency_inventory_sha256(),
        }
    )
    if not python_only:
        result["node_version"] = validate_node_version(_node_version())
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Fail-fast Local V1 reproducibility preflight"
    )
    parser.add_argument("--require-venv", action="store_true")
    parser.add_argument("--python-only", action="store_true")
    args = parser.parse_args()
    try:
        result = build_preflight_result(
            require_venv=args.require_venv,
            python_only=args.python_only,
        )
    except ReproducibilityPreflightError as exc:
        print(exc.gate_code, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
