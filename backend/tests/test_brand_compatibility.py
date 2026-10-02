"""Renaming must not abandon data or revive old access tokens."""
import uuid
from datetime import datetime, timezone

import pytest

from app.utils.branding import LEGACY_STATE_NAMESPACE, default_sqlite_url
from app.utils import token_blacklist


def test_fresh_sqlite_install_uses_new_name(tmp_path):
    assert default_sqlite_url(tmp_path).endswith("/autoteams.db")
    assert not list(tmp_path.iterdir())


def test_existing_sqlite_and_wal_are_selected_without_mutation(tmp_path):
    previous = tmp_path / f"{LEGACY_STATE_NAMESPACE}.db"
    wal = tmp_path / f"{LEGACY_STATE_NAMESPACE}.db-wal"
    previous.write_bytes(b"existing fixture data")
    wal.write_bytes(b"uncheckpointed fixture data")
    assert default_sqlite_url(tmp_path).endswith(f"/{previous.name}")
    assert previous.read_bytes() == b"existing fixture data"
    assert wal.read_bytes() == b"uncheckpointed fixture data"
    assert not (tmp_path / "autoteams.db").exists()


def test_existing_new_database_is_used(tmp_path):
    (tmp_path / "autoteams.db").write_bytes(b"new fixture")
    assert default_sqlite_url(tmp_path).endswith("/autoteams.db")


def test_ambiguous_sqlite_databases_require_explicit_selection(tmp_path):
    (tmp_path / "autoteams.db").touch()
    (tmp_path / f"{LEGACY_STATE_NAMESPACE}.db").touch()
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        default_sqlite_url(tmp_path)


def test_long_term_memory_collection_identity_is_stable():
    from app.services.memory.long_term import _MEMORY_NAMESPACE
    assert _MEMORY_NAMESPACE == uuid.uuid5(uuid.NAMESPACE_OID, f"{LEGACY_STATE_NAMESPACE}-memory-lt")


class RedisState:
    def __init__(self):
        self.values = {}
        self.writes = []

    def exists(self, key):
        return key in self.values

    def setex(self, key, ttl, value):
        self.values[key] = value
        self.writes.append((key, ttl))


@pytest.fixture
def revocation_state(monkeypatch):
    client = RedisState()
    payload = {"jti": "fixture-jti", "type": "access", "exp": datetime.now(timezone.utc).timestamp() + 300}
    monkeypatch.setattr(token_blacklist, "_get_redis", lambda: client)
    monkeypatch.setattr(token_blacklist, "_decode_for_revocation", lambda _: payload)
    return client


def test_old_redis_revocation_remains_effective(revocation_state):
    revocation_state.values[f"{LEGACY_STATE_NAMESPACE}:token_revoked:fixture-jti"] = "1"
    assert token_blacklist.is_token_revoked("fixture-token")


def test_new_revocation_blocks_new_and_old_instances(revocation_state):
    token_blacklist.revoke_access_token("fixture-token")
    legacy = f"{LEGACY_STATE_NAMESPACE}:token_revoked:fixture-jti"
    assert revocation_state.values[legacy] == "1"
    assert revocation_state.values["autoteams:token_revoked:fixture-jti"] == "1"
    assert revocation_state.writes[0][0] == legacy
    assert all(0 < ttl <= 300 for _, ttl in revocation_state.writes)
    assert token_blacklist.is_token_revoked("fixture-token")


def test_failed_second_write_still_blocks_both_reader_versions(revocation_state, monkeypatch):
    original = revocation_state.setex
    def fail_current(key, ttl, value):
        if key.startswith("autoteams:"):
            raise OSError("fixture unavailable")
        original(key, ttl, value)
    monkeypatch.setattr(revocation_state, "setex", fail_current)
    with pytest.raises(OSError):
        token_blacklist.revoke_access_token("fixture-token")
    assert revocation_state.exists(f"{LEGACY_STATE_NAMESPACE}:token_revoked:fixture-jti")
    assert token_blacklist.is_token_revoked("fixture-token")
