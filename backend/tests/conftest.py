"""Pytest defaults — keep tests off real MongoDB."""

from __future__ import annotations

import os

os.environ.setdefault("MONGODB_URI", "memory")
