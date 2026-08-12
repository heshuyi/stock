"""Isolated sync runner returns structured failure instead of killing API."""

from __future__ import annotations

import asyncio
import json

from app.services import sync_runner


def test_sync_isolated_reports_nonzero_exit(monkeypatch):
    class FakeProc:
        returncode = 139  # segfault-ish

        async def communicate(self):
            return b"", b"FATAL:address_pool_manager"

    async def fake_exec(*_a, **_k):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    out = asyncio.run(sync_runner.sync_all_isolated(use_mock=False, force=False))
    assert out["fetched"] == 0
    assert out["live"] is False
    assert "子进程异常退出" in (out.get("warning") or "")
    assert out.get("error_code") == 139


def test_sync_isolated_parses_stdout(monkeypatch):
    payload = {
        "synced_at": "t",
        "results": [],
        "skipped": 5,
        "incremental": 0,
        "fetched": 0,
        "rows_added": 0,
        "live": True,
        "force": False,
        "warning": "ok",
    }

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return json.dumps(payload).encode(), b""

    async def fake_exec(*_a, **_k):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    out = asyncio.run(sync_runner.sync_all_isolated(force=True))
    assert out["skipped"] == 5
    assert out["warning"] == "ok"
