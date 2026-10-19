"""Persistence exports, kept lazy to avoid runtime/telemetry import cycles."""

__all__ = ["RunNotFoundError", "SQLiteRunRepository"]

def __getattr__(name):
    if name == "SQLiteRunRepository":
        from .run_repository import SQLiteRunRepository
        return SQLiteRunRepository
    if name == "RunNotFoundError":
        from ..application.travel_service import RunNotFoundError
        return RunNotFoundError
    raise AttributeError(name)
