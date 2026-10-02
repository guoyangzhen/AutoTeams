"""The authenticated grant view exposes whether the setup token was consumed."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.enterprise import Enterprise
from app.models.user import User


@pytest.mark.asyncio
async def test_grant_view_tracks_claim_before_runner_connects(client, test_engine):
    registered_user = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "grant-claim-view@test.com",
            "name": "Grant owner",
            "password": "pass1234",
        },
    )
    assert registered_user.status_code == 201, registered_user.text
    user_id = registered_user.json()["data"]["user"]["id"]

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(Enterprise(id="ent-grant-claim-view", name="Grant claim test"))
        user = await session.get(User, user_id)
        user.enterprise_id = "ent-grant-claim-view"
        user.role = "admin"
        await session.commit()

    response = await client.post(
        "/api/v1/local-paths/register",
        json={"local_path": "D:/Test", "scope": "read"},
    )
    assert response.status_code == 201, response.text
    registration = response.json()["data"]
    grant_id = registration["grant"]["id"]
    assert registration["grant"]["status"] == "pending"
    assert registration["grant"]["claimed"] is False
    assert "setup_token_hash" not in registration["grant"]
    assert "setup_token_expires_at" not in registration["grant"]
    assert "setup_token" not in registration["grant"]

    claimed = await client.post(
        f"/api/v1/local-paths/{grant_id}/claim",
        json={"setup_token": registration["setup_token"]},
    )
    assert claimed.status_code == 200, claimed.text

    detail = await client.get(f"/api/v1/local-paths/{grant_id}")
    assert detail.status_code == 200, detail.text
    grant = detail.json()["data"]
    assert grant["status"] == "pending"
    assert grant["claimed"] is True
    assert "setup_token_hash" not in grant
    assert "setup_token_expires_at" not in grant
    assert "setup_token" not in grant

    listed = await client.get("/api/v1/local-paths")
    assert listed.status_code == 200, listed.text
    listed_grant = next(item for item in listed.json()["data"] if item["id"] == grant_id)
    assert listed_grant["status"] == "pending"
    assert listed_grant["claimed"] is True
