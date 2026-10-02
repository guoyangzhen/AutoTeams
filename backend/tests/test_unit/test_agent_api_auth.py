from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.agent_api import CreateAgentApiCredentialRequest
from app.utils.agent_api_auth import (
    AGENT_API_SCOPES,
    generate_agent_api_key,
    hash_agent_api_key,
    normalize_scopes,
)


def test_generated_agent_api_key_is_prefixed_and_not_persisted_as_plaintext():
    raw_key = generate_agent_api_key()

    assert raw_key.startswith("at_sk_")
    assert len(raw_key) > 40
    assert hash_agent_api_key(raw_key) == hash_agent_api_key(raw_key)
    assert raw_key not in hash_agent_api_key(raw_key)


def test_normalize_scopes_deduplicates_and_orders_allowed_values():
    scopes = normalize_scopes(["agent:chat", "agent:read", "agent:chat"])

    assert scopes == ["agent:chat", "agent:read"]
    assert set(scopes).issubset(AGENT_API_SCOPES)


def test_normalize_scopes_rejects_unknown_and_empty_scope_sets():
    with pytest.raises(ValueError, match="不支持"):
        normalize_scopes(["agent:read", "local:write"])
    with pytest.raises(ValueError, match="至少"):
        normalize_scopes([])


def test_credential_schema_rejects_past_expiry_and_cross_agent_duplicates():
    with pytest.raises(ValidationError, match="未来"):
        CreateAgentApiCredentialRequest(
            name="integration",
            scopes=["agent:read"],
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )

    with pytest.raises(ValidationError, match="重复"):
        CreateAgentApiCredentialRequest(
            name="integration",
            scopes=["agent:read"],
            allowed_agent_ids=["agent-1", "agent-1"],
        )
