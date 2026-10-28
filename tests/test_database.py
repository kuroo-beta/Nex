import asyncio
import time

import pytest

from nex.config import UserError
from nex.database import Database


async def test_registration_scope_and_unique_targets(db):
    await db.reserve(100, 10, 20, 30, 20)
    with pytest.raises(UserError):
        await db.reserve(100, 10, 21, 30, 20)
    await db.reserve(101, 10, 21, 30, 20)
    await db.reserve(100, 11, 22, 30, 20)
    with pytest.raises(UserError):
        await db.reserve(100, 12, 20, 30, 20)
    assert len(await db.rows("SELECT * FROM registrations")) == 3


async def test_concurrent_reservations_are_atomic(db):
    results = await asyncio.gather(
        *(db.reserve(100, 10, 20 + i, 30, 20) for i in range(15)), return_exceptions=True
    )
    assert sum(result is None for result in results) == 1
    assert all(result is None or isinstance(result, UserError) for result in results)


async def test_concurrent_independent_connections(tmp_path):
    path = str(tmp_path / "db.sqlite")
    first, second = Database(path), Database(path)
    await first.open()
    await second.open()
    try:
        results = await asyncio.gather(
            first.reserve(1, 2, 3, 4, 20), second.reserve(1, 2, 5, 4, 20), return_exceptions=True
        )
        assert sum(result is None for result in results) == 1
    finally:
        await first.close()
        await second.close()


async def test_expired_reservation_releases_slot(db):
    await db.reserve(100, 10, 20, 30, 20)
    await db.execute("UPDATE registrations SET expires=?", (time.time() - 1,))
    await db.reserve(100, 10, 21, 30, 20)
    assert (await db.registrations(100, "pending"))[0]["bot_id"] == "21"


async def test_schema_and_data_survive_restart(tmp_path):
    path = str(tmp_path / "state.sqlite")
    first = Database(path)
    await first.open()
    await first.reserve(100, 10, 20, 30, 20)
    await first.close()
    second = Database(path)
    await second.open()
    try:
        assert len(await second.registrations(100, "pending")) == 1
        assert await second.rows("SELECT version FROM schema_version") == [{"version": 1}]
    finally:
        await second.close()


async def test_transaction_rollback(db):
    with pytest.raises(RuntimeError):
        async with db.transaction() as connection:
            await connection.execute("INSERT INTO trusted VALUES('100','10','purge')")
            raise RuntimeError("simulated interrupted transaction")
    assert not await db.rows("SELECT * FROM trusted")
