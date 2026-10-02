"""Use AutoTeams runtime role names without changing ACLs or applied migrations.

Revision ID: e7f8a9b0c1d2
Revises: d5e6f7a8b9c0

PostgreSQL roles retain their OIDs, grants, RLS policies and SCRAM passwords.
Existing target roles, memberships, owned objects or use in another database
are rejected before any rename. Operators must stop old application processes
and update connection usernames together; the migration CLI resets passwords.
SQLite has no database roles and is unchanged.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Historical names occur only at this explicit migration boundary.
ROLE_PAIRS = (
    ("autofde_app", "autoteams_app"),
    ("autofde_worker", "autoteams_worker"),
    ("autofde_bootstrap", "autoteams_bootstrap"),
)


def _rename_roles(pairs: tuple[tuple[str, str], ...], *, upgrading: bool) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # Role names are cluster-scoped: serialize concurrent brand migrations.
    bind.execute(sa.text("SELECT pg_advisory_xact_lock(7093020261900)"))
    for source, target in pairs:
        role = bind.execute(sa.text("""
            SELECT oid, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole,
                   rolreplication, rolinherit, rolcanlogin
            FROM pg_roles WHERE rolname = :source
        """), {"source": source}).mappings().one_or_none()
        if role is None:
            raise RuntimeError(f"AutoTeams role migration: required role {source} is missing")
        if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :target"), {"target": target}).scalar():
            raise RuntimeError(f"AutoTeams role migration: target role {target} already exists; refusing to merge identities")
        oid = role["oid"]
        if upgrading and (not role["rolcanlogin"] or any(role[field] for field in (
            "rolsuper", "rolbypassrls", "rolcreatedb", "rolcreaterole", "rolreplication", "rolinherit",
        ))):
            raise RuntimeError(f"AutoTeams role migration: unsafe attributes on {source}")
        unsafe = bind.execute(sa.text("""
            SELECT EXISTS (SELECT 1 FROM pg_auth_members WHERE member = :oid OR roleid = :oid)
                OR EXISTS (SELECT 1 FROM pg_class WHERE relowner = :oid)
                OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = :oid)
                OR EXISTS (SELECT 1 FROM pg_proc WHERE proowner = :oid)
                OR EXISTS (SELECT 1 FROM pg_database WHERE datdba = :oid)
                OR EXISTS (
                    SELECT 1 FROM pg_shdepend
                    WHERE refclassid = 'pg_authid'::regclass AND refobjid = :oid
                      AND (
                        (dbid <> 0 AND dbid <> (SELECT oid FROM pg_database WHERE datname = current_database()))
                        OR (dbid = 0 AND classid = 'pg_database'::regclass
                            AND objid <> (SELECT oid FROM pg_database WHERE datname = current_database()))
                      )
                )
        """), {"oid": oid}).scalar()
        if unsafe:
            raise RuntimeError(f"AutoTeams role migration: {source} has memberships, ownership or use in another database")
    # Check all three identities before changing any name. Names are fixed above,
    # never taken from environment variables or request input.
    for source, target in pairs:
        op.execute(f'ALTER ROLE "{source}" RENAME TO "{target}"')  # noqa: S608


def upgrade() -> None:
    _rename_roles(ROLE_PAIRS, upgrading=True)


def downgrade() -> None:
    _rename_roles(tuple((target, source) for source, target in ROLE_PAIRS), upgrading=False)
