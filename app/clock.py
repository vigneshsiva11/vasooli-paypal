"""Injectable system and simulated clock abstraction."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Protocol, runtime_checkable
import logging

from app.db import get_database

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@runtime_checkable
class Clock(Protocol):
    """Clock interface returning timezone-aware UTC datetime."""

    def now(self) -> datetime: ...
    def is_simulated(self) -> bool: ...
    @property
    def offset_seconds(self) -> float: ...


class SystemClock:
    """Standard system clock."""

    def now(self) -> datetime:
        return _utc_now()

    def is_simulated(self) -> bool:
        return False

    @property
    def offset_seconds(self) -> float:
        return 0.0


class SimulatedClock:
    """Explicit in-memory simulated clock for tests and unit evaluations."""

    def __init__(self, base_time: datetime | None = None, offset_seconds: float = 0.0):
        self._base_time = base_time or _utc_now()
        self._offset_seconds = float(offset_seconds)

    def advance(self, hours: float = 0.0, seconds: float = 0.0) -> None:
        self._offset_seconds += float(hours) * 3600.0 + float(seconds)

    def now(self) -> datetime:
        return self._base_time + timedelta(seconds=self._offset_seconds)

    def is_simulated(self) -> bool:
        return True

    @property
    def offset_seconds(self) -> float:
        return self._offset_seconds


class MongoClock:
    """Clock that queries or loads offset from MongoDB sim_clock collection."""

    def __init__(self, offset_seconds: float = 0.0, simulated: bool = False):
        self._offset_seconds = float(offset_seconds)
        self._simulated = simulated

    def now(self) -> datetime:
        return _utc_now() + timedelta(seconds=self._offset_seconds)

    def is_simulated(self) -> bool:
        return self._simulated

    @property
    def offset_seconds(self) -> float:
        return self._offset_seconds

    @classmethod
    async def load(cls, db: Any = None) -> "MongoClock":
        """Load simulated clock state from MongoDB."""
        try:
            database = db if db is not None else get_database()
            doc = await database["sim_clock"].find_one({"_id": "default"})
            if doc and doc.get("is_active"):
                return cls(
                    offset_seconds=float(doc.get("offset_seconds", 0.0)),
                    simulated=True,
                )
        except Exception as exc:
            logger.debug("Failed to read sim_clock from database: %s", exc)
        return cls(offset_seconds=0.0, simulated=False)


async def get_clock(db: Any = None) -> Clock:
    """Return the active clock (MongoClock loaded from DB)."""
    return await MongoClock.load(db)


async def advance_mongo_clock(hours: float, db: Any = None) -> MongoClock:
    database = db if db is not None else get_database()
    await database["sim_clock"].update_one(
        {"_id": "default"},
        {
            "$inc": {"offset_seconds": float(hours) * 3600.0},
            "$set": {"is_active": True, "updated_at": _utc_now()},
        },
        upsert=True,
    )
    return await MongoClock.load(database)


async def reset_mongo_clock(db: Any = None) -> MongoClock:
    database = db if db is not None else get_database()
    await database["sim_clock"].update_one(
        {"_id": "default"},
        {
            "$set": {"offset_seconds": 0.0, "is_active": False, "updated_at": _utc_now()},
        },
        upsert=True,
    )
    return await MongoClock.load(database)


async def set_mongo_clock_offset(offset_seconds: float, db: Any = None) -> MongoClock:
    database = db if db is not None else get_database()
    is_active = abs(offset_seconds) > 0.001
    await database["sim_clock"].update_one(
        {"_id": "default"},
        {
            "$set": {
                "offset_seconds": float(offset_seconds),
                "is_active": is_active,
                "updated_at": _utc_now(),
            }
        },
        upsert=True,
    )
    return await MongoClock.load(database)

