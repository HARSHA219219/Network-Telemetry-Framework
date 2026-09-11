"""
FastAPI dependencies. The session factory lives on app.state (set at
startup in backend/main.py, or by tests via TestClient(app) after
assigning app.state.session_factory) rather than being constructed
inline here - this is what lets tests swap in an in-memory sqlite
session factory without touching route code.
"""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session


def get_session(request: Request) -> Generator[Session, None, None]:
    session_factory = request.app.state.session_factory
    with session_factory() as session:
        yield session
