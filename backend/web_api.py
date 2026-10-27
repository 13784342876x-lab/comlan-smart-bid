"""Convenience module for ``uvicorn web_api:app`` from the backend directory."""

from app.web.api import app

__all__ = ["app"]
