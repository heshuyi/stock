#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Clash/Verge 等本地代理会劫持 localhost，导致页面接口全部 failed
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy || true
export NO_PROXY=localhost,127.0.0.1,::1
export MONGODB_URI="${MONGODB_URI:-memory}"
export USE_MOCK_DATA="${USE_MOCK_DATA:-false}"
export CONFIG_PATH="${CONFIG_PATH:-$ROOT/configs/symbols.json}"
export MARKET_DB_PATH="${MARKET_DB_PATH:-$ROOT/backend/data/market.db}"
export PYTHONPATH="$ROOT/backend"
export API_URL="${API_URL:-http://127.0.0.1:8000}"
export CORS_ORIGINS="${CORS_ORIGINS:-http://localhost:3000,http://127.0.0.1:3000}"
export STOCK_SYNC_CONCURRENCY="${STOCK_SYNC_CONCURRENCY:-1}"

pkill -f "uvicorn app.main:app" 2>/dev/null || true
pkill -f "scripts/api_watchdog.py" 2>/dev/null || true
pkill -f "next-server\|next dev" 2>/dev/null || true
pkill -f "next dev --port 3000" 2>/dev/null || true
sleep 1

# Detach with start_new_session so Cursor/agent shell exit does not kill services
STOCK_ROOT="$ROOT" python3 <<'PY'
import os
import subprocess
from pathlib import Path

root = Path(os.environ["STOCK_ROOT"])
venv_python = root / "backend" / ".venv" / "bin" / "python"
env = os.environ.copy()

api = subprocess.Popen(
    [
        str(venv_python),
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ],
    cwd=str(root / "backend"),
    env=env,
    stdout=open("/tmp/stock-api.log", "w"),
    stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL,
    start_new_session=True,
)
Path("/tmp/stock-api.pid").write_text(str(api.pid))
print(f"API pid {api.pid}")

watchdog = subprocess.Popen(
    [str(venv_python), str(root / "scripts" / "api_watchdog.py")],
    cwd=str(root),
    env=env,
    stdout=open("/tmp/stock-api-watchdog.log", "w"),
    stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL,
    start_new_session=True,
)
Path("/tmp/stock-api-watchdog.pid").write_text(str(watchdog.pid))
print(f"WATCHDOG pid {watchdog.pid}")

web = subprocess.Popen(
    ["npm", "run", "dev", "--", "--port", "3000", "--hostname", "127.0.0.1"],
    cwd=str(root / "frontend"),
    env=env,
    stdout=open("/tmp/stock-web.log", "w"),
    stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL,
    start_new_session=True,
)
Path("/tmp/stock-web.pid").write_text(str(web.pid))
print(f"WEB pid {web.pid}")
PY

ok_api=0
ok_web=0
for _ in $(seq 1 25); do
  if curl --noproxy '*' -sf -o /dev/null http://127.0.0.1:8000/api/health; then
    ok_api=1
  fi
  if curl --noproxy '*' -sf -o /dev/null http://127.0.0.1:3000/; then
    ok_web=1
  fi
  if [[ "$ok_api" -eq 1 && "$ok_web" -eq 1 ]]; then
    break
  fi
  sleep 1
done

if [[ "$ok_api" -ne 1 ]]; then
  echo "API failed to start. Last log:"
  tail -30 /tmp/stock-api.log || true
  exit 1
fi
if [[ "$ok_web" -ne 1 ]]; then
  echo "WEB failed to start. Last log:"
  tail -40 /tmp/stock-web.log || true
  exit 1
fi

curl --noproxy '*' -sf http://127.0.0.1:8000/api/health && echo
curl --noproxy '*' -sf -o /dev/null -w "today:%{http_code}\n" http://127.0.0.1:3000/api/dashboard/today
echo "Open http://127.0.0.1:3000"
echo "PIDs: /tmp/stock-api.pid /tmp/stock-web.pid /tmp/stock-api-watchdog.pid"
