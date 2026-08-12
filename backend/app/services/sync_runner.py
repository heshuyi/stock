"""Run market sync in an isolated subprocess.

akshare pulls py_mini_racer (V8). On some Python/macOS builds a second init
aborts the whole process with:
  FATAL:address_pool_manager.cc Check failed: !pool->IsInitialized()

Keeping network sync out of the uvicorn process so the API stays up for
dashboard / settings even when a sync child dies.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _empty_failure(
    *,
    force: bool,
    code: int | None,
    detail: str,
) -> dict[str, Any]:
    return {
        "synced_at": datetime.utcnow().isoformat() + "Z",
        "results": [],
        "purged": [],
        "skipped": 0,
        "incremental": 0,
        "fetched": 0,
        "rows_added": 0,
        "live": False,
        "force": force,
        "warning": detail,
        "error_code": code,
        "data_status": None,
    }


async def sync_all_isolated(
    use_mock: bool | None = None,
    *,
    force: bool = False,
    timeout_sec: float = 600.0,
) -> dict[str, Any]:
    """Invoke ``python -m app.services.sync_runner`` and return its JSON payload."""
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(_BACKEND_ROOT)
        if not env.get("PYTHONPATH")
        else os.pathsep.join([str(_BACKEND_ROOT), env["PYTHONPATH"]])
    )
    # Child must be serial: parallel akshare + mini_racer is what aborts.
    env["STOCK_SYNC_CONCURRENCY"] = "1"

    args = [sys.executable, "-m", "app.services.sync_runner"]
    if force:
        args.append("--force")
    if use_mock is True:
        args.append("--mock")
    elif use_mock is False:
        args.append("--live")

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_BACKEND_ROOT),
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        logger.exception("failed to spawn sync subprocess")
        return _empty_failure(
            force=force,
            code=None,
            detail=f"无法启动行情同步子进程：{exc}",
        )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_sec
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return _empty_failure(
            force=force,
            code=-1,
            detail=f"行情同步超时（>{int(timeout_sec)}s），已终止子进程；API 仍可用",
        )

    err_text = (stderr or b"").decode("utf-8", errors="replace").strip()
    out_text = (stdout or b"").decode("utf-8", errors="replace").strip()
    if err_text:
        logger.warning(
            "sync subprocess stderr (tail): %s", err_text[-2000:]
        )

    if proc.returncode != 0:
        detail = (
            f"行情同步子进程异常退出（code={proc.returncode}）。"
            "多为 akshare/mini_racer 原生崩溃；本地行情仓仍可用于看板，请稍后重试。"
        )
        if err_text:
            # Keep payload small for the UI
            last = err_text.splitlines()[-1][:200]
            detail = f"{detail} 日志：{last}"
        return _empty_failure(force=force, code=proc.returncode, detail=detail)

    if not out_text:
        return _empty_failure(
            force=force,
            code=proc.returncode,
            detail="行情同步子进程无输出",
        )

    try:
        payload = json.loads(out_text)
    except json.JSONDecodeError:
        # Some libraries print to stdout; try last JSON object line
        for line in reversed(out_text.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    payload = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        else:
            return _empty_failure(
                force=force,
                code=proc.returncode,
                detail="行情同步结果无法解析为 JSON",
            )

    if not isinstance(payload, dict):
        return _empty_failure(
            force=force,
            code=proc.returncode,
            detail="行情同步结果格式无效",
        )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Isolated market sync worker")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.mock and args.live:
        parser.error("use only one of --mock / --live")
    use_mock: bool | None
    if args.mock:
        use_mock = True
    elif args.live:
        use_mock = False
    else:
        use_mock = None

    logging.basicConfig(level=logging.INFO)

    from app.services import market_store
    from app.services.market_data import sync_all

    market_store.ensure_store()
    concurrency = int(os.environ.get("STOCK_SYNC_CONCURRENCY", "1"))
    result = asyncio.run(
        sync_all(use_mock=use_mock, force=args.force, concurrency=concurrency)
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
