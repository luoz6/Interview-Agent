from __future__ import print_function

import argparse
import importlib.metadata
from pathlib import Path
import subprocess
import sys

from scripts.reproducibility_preflight import (
    PYTHON_ENVIRONMENT_MISMATCH,
    PYTHON_VERSION_UNSUPPORTED,
    ReproducibilityPreflightError,
    validate_python_environment,
)


PIP_TOOLS_VERSION = "7.6.0"
PIP_VERSION = "25.1.1"
LOCK_COMMAND_TEMPLATE = (
    "python -m piptools compile --allow-unsafe --generate-hashes "
    "--resolver=backtracking --no-strip-extras --newline=lf "
    "--output-file={output_file} requirements.txt"
)


def validate_generator_environment():
    validate_python_environment(
        version_info=sys.version_info,
        executable=sys.executable,
        prefix=sys.prefix,
        base_prefix=sys.base_prefix,
        require_venv=False,
    )
    try:
        version = importlib.metadata.version("pip-tools")
        pip_version = importlib.metadata.version("pip")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ReproducibilityPreflightError(
            PYTHON_ENVIRONMENT_MISMATCH
        ) from exc
    if version != PIP_TOOLS_VERSION or pip_version != PIP_VERSION:
        raise ReproducibilityPreflightError(PYTHON_ENVIRONMENT_MISMATCH)


def compile_lock(*, output_file):
    validate_generator_environment()
    command = [
        sys.executable,
        "-m",
        "piptools",
        "compile",
        "--allow-unsafe",
        "--generate-hashes",
        "--resolver=backtracking",
        "--no-strip-extras",
        "--newline=lf",
        "--output-file",
        str(output_file),
        "requirements.txt",
    ]
    subprocess.run(command, check=True)


def main():
    parser = argparse.ArgumentParser(description="Generate requirements lock")
    parser.add_argument(
        "--output-file",
        default="requirements.lock.txt",
        type=Path,
    )
    args = parser.parse_args()
    try:
        compile_lock(output_file=args.output_file)
    except ReproducibilityPreflightError as exc:
        print(exc.gate_code, file=sys.stderr)
        return 1
    except subprocess.CalledProcessError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
