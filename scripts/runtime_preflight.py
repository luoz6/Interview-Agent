import argparse
import json
import re
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

from app.services.config import get_redis_url


class PreflightError(RuntimeError):
    pass


def validate_runtime_versions(
    *, python_version: tuple[int, int, int], node_version: str
) -> dict[str, str]:
    if python_version[:2] != (3, 11):
        raise PreflightError(
            f"Python 3.11 is required; found {'.'.join(map(str, python_version))}"
        )
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", node_version.strip())
    if match is None or int(match.group(1)) not in {20, 22}:
        raise PreflightError(
            f"Node.js 20 or 22 LTS is required; found {node_version.strip()}"
        )
    return {
        "python": ".".join(map(str, python_version)),
        "node": ".".join(match.groups()),
    }


def redact_connection_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.password is None:
        return value
    username = parsed.username or ""
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    netloc = f"{username}:***@{host}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def check_redis(client, *, key: str = "stage41:preflight", ttl_seconds: int = 30) -> dict:
    value = "ok"
    try:
        ping = bool(client.ping())
        client.set(key, value, ex=ttl_seconds)
        stored = client.get(key)
        if isinstance(stored, bytes):
            stored = stored.decode("utf-8")
        ttl = client.ttl(key)
        return {
            "ping": ping,
            "read_write": stored == value,
            "ttl": 0 < int(ttl) <= ttl_seconds,
        }
    finally:
        client.delete(key)


def _node_version() -> str:
    try:
        return subprocess.check_output(
            ["node", "--version"], text=True, stderr=subprocess.STDOUT
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PreflightError("Node.js 20 is required but node is unavailable") from exc


def _redis_client(url: str):
    try:
        from redis import Redis
    except ImportError as exc:
        raise PreflightError("redis package is required for the Celery profile") from exc
    return Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Local V1 prerequisites")
    parser.add_argument("--profile", choices=("core", "celery"), default="core")
    args = parser.parse_args()
    try:
        result = validate_runtime_versions(
            python_version=sys.version_info[:3], node_version=_node_version()
        )
        result["profile"] = args.profile
        if args.profile == "celery":
            url = get_redis_url()
            result["redis_url"] = redact_connection_url(url)
            result["redis"] = check_redis(_redis_client(url))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
