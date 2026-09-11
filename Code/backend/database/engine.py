"""
Database engine + session factory. Connection details come exclusively
from environment variables (DATABASE_URL, or the individual POSTGRES_*
vars) - never hardcoded, per project security requirements.

Production (Docker Compose) uses PostgreSQL. Tests use an in-memory
SQLite database created via create_session_factory("sqlite:///:memory:")
so the ORM/service/alert-engine logic is fully unit-testable without a
running Postgres instance - the same pattern already used for SNMP
(mock transport) and gRPC (fake stub) in earlier phases.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def get_database_url() -> str:
    explicit_url = os.environ.get("DATABASE_URL")
    if explicit_url:
        return explicit_url

    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    database = os.environ.get("POSTGRES_DB", "telemetry")
    user = os.environ.get("POSTGRES_USER", "telemetry_user")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"


def create_session_factory(database_url: str | None = None):
    """Create the engine, ensure tables exist, and return a session factory.

    Importing backend.models.orm before calling this ensures all models
    are registered on Base.metadata.

    For in-memory SQLite specifically, StaticPool is required: SQLAlchemy's
    default SQLite pooling hands each thread its own connection, and an
    in-memory database only exists for the lifetime of one connection - a
    second thread would see a fresh, empty (tableless) database. This
    matters in practice once FastAPI's TestClient is involved, since it
    runs synchronous route handlers in a worker thread distinct from the
    thread that called create_session_factory() and created the tables.
    File-based SQLite and PostgreSQL are unaffected either way.
    """
    from sqlalchemy.pool import StaticPool

    from backend.models import orm  # noqa: F401  (registers models on Base.metadata)

    url = database_url or get_database_url()
    is_memory_sqlite = url.startswith("sqlite") and ":memory:" in url

    engine_kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    if is_memory_sqlite:
        engine_kwargs["poolclass"] = StaticPool

    engine = create_engine(url, **engine_kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
