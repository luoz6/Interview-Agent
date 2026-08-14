"""Compatibility import surface for RAG API contracts.

Definitions live in the application contract module so application services never
depend on the FastAPI adapter layer.
"""

from app.application.knowledge.diagnostic_models import *  # noqa: F403
