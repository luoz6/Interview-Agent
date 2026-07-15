import pytest

from scripts.runtime_preflight import (
    PreflightError,
    check_redis,
    redact_connection_url,
    validate_runtime_versions,
)


def test_runtime_versions_accept_supported_python_and_node():
    result = validate_runtime_versions(python_version=(3, 11, 9), node_version="v20.18.0")

    assert result == {"python": "3.11.9", "node": "20.18.0"}


def test_runtime_versions_accept_node_22_lts():
    result = validate_runtime_versions(python_version=(3, 11, 9), node_version="v22.21.0")

    assert result == {"python": "3.11.9", "node": "22.21.0"}


@pytest.mark.parametrize(
    ("python_version", "node_version", "message"),
    [
        ((3, 8, 3), "v20.18.0", "Python 3.11"),
        ((3, 11, 9), "v21.7.0", "Node.js 20 or 22"),
    ],
)
def test_runtime_versions_reject_unsupported_versions(
    python_version, node_version, message
):
    with pytest.raises(PreflightError, match=message):
        validate_runtime_versions(
            python_version=python_version,
            node_version=node_version,
        )


def test_redis_smoke_pings_sets_ttl_and_cleans_up():
    class FakeRedis:
        def __init__(self):
            self.deleted = []
            self.expires_in = None

        def ping(self):
            return True

        def set(self, key, value, ex):
            self.key = key
            self.value = value
            self.expires_in = ex
            return True

        def get(self, key):
            assert key == self.key
            return self.value.encode()

        def ttl(self, key):
            assert key == self.key
            return self.expires_in

        def delete(self, key):
            self.deleted.append(key)

    client = FakeRedis()

    result = check_redis(client, key="stage41:smoke", ttl_seconds=30)

    assert result == {"ping": True, "read_write": True, "ttl": True}
    assert client.deleted == ["stage41:smoke"]


def test_redact_connection_url_hides_password():
    assert (
        redact_connection_url("redis://user:secret@127.0.0.1:6379/0")
        == "redis://user:***@127.0.0.1:6379/0"
    )
