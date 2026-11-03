"""Alembic up/down against CI Postgres (skipped without TEST_DATABASE_URL)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


pytestmark = pytest.mark.postgres


def test_migrations_up_down():
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    env = dict(os.environ)
    env["TEST_DATABASE_URL"] = url
    env["DATABASE_URL"] = url

    def alembic(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
        )

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr
    down = alembic("downgrade", "base")
    assert down.returncode == 0, down.stdout + down.stderr
    up2 = alembic("upgrade", "head")
    assert up2.returncode == 0, up2.stdout + up2.stderr

    import asyncio
    import asyncpg
    from talking.db import normalize_async_url

    raw = url
    if raw.startswith("postgresql+asyncpg://"):
        raw = "postgresql://" + raw.split("://", 1)[1]

    async def check():
        conn = await asyncpg.connect(raw)
        try:
            tables = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"
            )
            names = {r["tablename"] for r in tables}
            for expected in (
                "users", "identities", "sessions", "recovery_codes",
                "auth_events", "login_challenges", "webauthn_credentials",
                "alembic_version",
            ):
                assert expected in names, names
            comment = await conn.fetchval(
                "SELECT obj_description('auth_events'::regclass)"
            )
            assert comment and "90" in comment
            pk_comment = await conn.fetchval(
                "SELECT obj_description('webauthn_credentials'::regclass)"
            )
            assert pk_comment and "Passkey" in pk_comment
        finally:
            await conn.close()

    asyncio.run(check())

    from talking.auth.crypto import new_id
    from talking.auth.storage import PostgresAuthStorage
    from talking.auth.types import CredentialRecord, IdentityRecord, UserRecord
    from talking.db import make_engine, make_session_factory

    engine = make_engine(url)
    store = PostgresAuthStorage(make_session_factory(engine))

    async def roundtrip():
        user = UserRecord(id=new_id())
        await store.create_user(user)
        ident = IdentityRecord(
            id=new_id(), user_id=user.id, provider="passkey", subject="abc" * 10,
        )
        await store.create_identity(ident)
        cred = CredentialRecord(
            id=new_id(), user_id=user.id, identity_id=ident.id,
            credential_id=b"cred-bytes-example-0001", public_key=b"\x01\x02cose",
            sign_count=3, transports=["internal"], aaguid="0" * 36,
        )
        await store.create_credential(cred)
        found = await store.get_credential_by_credential_id(b"cred-bytes-example-0001")
        assert found is not None
        assert found.sign_count == 3
        assert found.identity_id == ident.id
        found.sign_count = 4
        await store.save_credential(found)
        listed = await store.list_credentials(user.id)
        assert listed[0].sign_count == 4
        await store.delete_identity(ident.id)
        assert await store.get_credential_by_identity_id(ident.id) is None
        await engine.dispose()

    asyncio.run(roundtrip())
