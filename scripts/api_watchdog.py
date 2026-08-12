#!/usr/bin/env python3
"""Restart uvicorn if /api/health stops responding.

akshare/mini_racer can still kill an in-process worker in edge cases; this
watchdog brings the API back so the Next.js proxy stops returning 500.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / "backend" / ".venv" / "bin" / "python"
API_LOG = Path("/tmp/stock-api.log")
API_PID = Path("/tmp/stock-api.pid")
HEALTH = "http://127.0.0.1:8000/api/health"
INTERVAL = float(os.environ.get("STOCK_API_WATCHDOG_INTERVAL", "4"))
FAILS_BEFORE_RESTART = int(os.environ.get("STOCK_API_WATCHDOG_FAILS", "2"))


def _health_ok() -> bool:
    try:
        req = urllib.request.Request(HEALTH, method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid() -> int | None:
    try:
        raw = API_PID.read_text().strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None


def _kill_api() -> None:
    pid = _read_pid()
    if pid and _pid_alive(pid):
        try:
            os.killpg(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
        time.sleep(1)
        if _pid_alive(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                try:
                    os.kill(pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
    # Also clear stray uvicorn on 8000
    subprocess.run(
        ["pkill", "-f", "uvicorn app.main:app"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1)


def _start_api() -> int:
    env = os.environ.copy()
    env.setdefault("MONGODB_URI", "memory")
    env.setdefault("PYTHONPATH", str(ROOT / "backend"))
    env.setdefault("CONFIG_PATH", str(ROOT / "configs" / "symbols.json"))
    env.setdefault("MARKET_DB_PATH", str(ROOT / "backend" / "data" / "market.db"))
    env.setdefault("API_URL", "http://127.0.0.1:8000")
    env.setdefault(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    )
    # Drop proxy for localhost
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        env.pop(key, None)
    env["NO_PROXY"] = "localhost,127.0.0.1,::1"

    log_f = open(API_LOG, "a", buffering=1)
    proc = subprocess.Popen(
        [
            str(VENV_PYTHON),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=str(ROOT / "backend"),
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    API_PID.write_text(str(proc.pid))
    print(f"[watchdog] restarted API pid={proc.pid}", flush=True)
    return proc.pid


def main() -> int:
    if not VENV_PYTHON.exists():
        print(f"[watchdog] missing venv python: {VENV_PYTHON}", flush=True)
        return 1
    fails = 0
    print("[watchdog] monitoring", HEALTH, flush=True)
    while True:
        time.sleep(INTERVAL)
        if _health_ok():
            fails = 0
            continue
        fails += 1
        print(f"[watchdog] health fail {fails}/{FAILS_BEFORE_RESTART}", flush=True)
        if fails < FAILS_BEFORE_RESTART:
            continue
        print("[watchdog] restarting API…", flush=True)
        _kill_api()
        _start_api()
        # give uvicorn a moment before counting more fails
        time.sleep(3)
        fails = 0 if _health_ok() else FAILS_BEFORE_RESTART


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
