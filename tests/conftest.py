"""Shared pytest configuration for lightweight, offline test runs."""

from __future__ import annotations

import os
import tempfile

# UMAP imports numba-decorated functions during collection. In some local and
# CI environments numba cannot cache inside the virtualenv package directory, so
# point it at a writable temp directory before any tests import pipeline.cluster.
os.environ.setdefault(
    "NUMBA_CACHE_DIR",
    os.path.join(tempfile.gettempdir(), "ai-alpha-radar-numba"),
)
