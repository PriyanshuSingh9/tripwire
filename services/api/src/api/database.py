"""Case review database: async SQLite engine, sessions, and schema setup.

The database path comes from ``TRACEAML_CASE_DB_PATH`` (relative paths
resolve against the repository root). The review history table is
append-only at the storage layer: SQLite triggers abort any UPDATE or DELETE
on ``case_reviews`` and any DELETE on ``cases``, so dismissal or a buggy code
path can never erase provenance.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy ORM models."""


APPEND_ONLY_TRIGGERS: tuple[str, ...] = (
    """
    CREATE TRIGGER IF NOT EXISTS case_reviews_no_update
    BEFORE UPDATE ON case_reviews
    BEGIN SELECT RAISE(ABORT, 'case review history is append-only'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS case_reviews_no_delete
    BEFORE DELETE ON case_reviews
    BEGIN SELECT RAISE(ABORT, 'case review history is append-only'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS case_evidence_no_update
    BEFORE UPDATE ON case_evidence
    BEGIN SELECT RAISE(ABORT, 'case evidence is append-only'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS case_evidence_no_delete
    BEFORE DELETE ON case_evidence
    BEGIN SELECT RAISE(ABORT, 'case evidence is append-only'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS cases_no_delete
    BEFORE DELETE ON cases
    BEGIN SELECT RAISE(ABORT, 'cases are never deleted'); END
    """,
    # At most one active (open or confirmed) case per transaction, enforced
    # atomically; dismissed cases do not count and remain in history.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_cases_active_transaction
    ON cases (transaction_id)
    WHERE status IN ('suspected', 'under_review', 'confirmed')
    """,
)


class CaseDatabase:
    """Owns the async engine for one SQLite file.

    ``NullPool`` opens a connection per session, which keeps the engine safe
    to use from any event loop (test clients and the dev server differ).
    Construction does no IO; ``initialize`` creates the directory, tables,
    and append-only triggers idempotently.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.engine: AsyncEngine = create_async_engine(
            f"sqlite+aiosqlite:///{path.as_posix()}",
            poolclass=NullPool,
        )
        self.sessions = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        self._ready = False

    async def initialize(self) -> None:
        """Create the schema and triggers once per process."""
        if self._ready:
            return
        # Importing the models registers their tables on Base.metadata.
        from api.cases import models  # noqa: F401

        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            for statement in APPEND_ONLY_TRIGGERS:
                await connection.execute(text(statement))
        self._ready = True

    async def close(self) -> None:
        """Dispose engine resources."""
        await self.engine.dispose()
