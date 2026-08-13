from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
import subprocess
from urllib.parse import unquote, urlparse


@dataclass(frozen=True)
class PostgresToolInvocation:
    command: tuple[str, ...]
    env: dict[str, str] | None
    transport: str


def _connection_arguments(dsn: str) -> tuple[list[str], dict[str, str] | None]:
    parsed = urlparse(dsn)
    database = parsed.path.lstrip("/")
    user = unquote(parsed.username or "postgres")
    if not database:
        raise RuntimeError("POSTGRES_DSN must name a database")
    arguments = ["-U", user, "-d", unquote(database)]
    if parsed.hostname:
        arguments.extend(["-h", parsed.hostname])
    if parsed.port:
        arguments.extend(["-p", str(parsed.port)])
    env = None
    if parsed.password is not None:
        env = dict(os.environ)
        env["PGPASSWORD"] = unquote(parsed.password)
    return arguments, env


def postgres_tool_version(tool: str, *, container: str) -> tuple[str, str]:
    executable = shutil.which(tool)
    if executable:
        command = [executable, "--version"]
        transport = "native"
    else:
        docker = shutil.which("docker")
        if not docker or not container.strip():
            raise RuntimeError("PostgreSQL backup tools are unavailable")
        command = [docker, "exec", container, tool, "--version"]
        transport = "docker"
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        raise RuntimeError("PostgreSQL backup tools are unavailable") from exc
    if completed.returncode != 0 or tool not in completed.stdout:
        raise RuntimeError("PostgreSQL backup tools are unavailable")
    return completed.stdout.strip(), transport


def build_pg_dump_invocation(
    dsn: str,
    *,
    container: str,
    table_names: tuple[str, ...],
) -> PostgresToolInvocation:
    executable = shutil.which("pg_dump")
    if executable:
        connection, env = _connection_arguments(dsn)
        command = [
            executable,
            *connection,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
        ]
        transport = "native"
    else:
        docker = shutil.which("docker")
        if not docker or not container.strip():
            raise RuntimeError("PostgreSQL pg_dump is unavailable")
        parsed = urlparse(dsn)
        database = parsed.path.lstrip("/")
        user = unquote(parsed.username or "postgres")
        if not database:
            raise RuntimeError("POSTGRES_DSN must name a database")
        command = [
            docker,
            "exec",
            container,
            "pg_dump",
            "-U",
            user,
            "-d",
            unquote(database),
            "--format=custom",
            "--no-owner",
            "--no-privileges",
        ]
        env = None
        transport = "docker"
    for table_name in table_names:
        command.extend(["--table", f"public.{table_name}"])
    return PostgresToolInvocation(tuple(command), env, transport)


def build_pg_restore_invocation(
    dsn: str,
    *,
    container: str,
) -> PostgresToolInvocation:
    executable = shutil.which("pg_restore")
    if executable:
        connection, env = _connection_arguments(dsn)
        command = [
            executable,
            *connection,
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
        ]
        transport = "native"
    else:
        docker = shutil.which("docker")
        if not docker or not container.strip():
            raise RuntimeError("PostgreSQL pg_restore is unavailable")
        parsed = urlparse(dsn)
        database = parsed.path.lstrip("/")
        user = unquote(parsed.username or "postgres")
        if not database:
            raise RuntimeError("POSTGRES_DSN must name a database")
        command = [
            docker,
            "exec",
            "-i",
            container,
            "pg_restore",
            "-U",
            user,
            "-d",
            unquote(database),
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
        ]
        env = None
        transport = "docker"
    return PostgresToolInvocation(tuple(command), env, transport)
