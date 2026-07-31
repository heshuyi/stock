# A股宽基定投投顾看板

个人本地投顾看板：每周估值定投（60%）+ 趋势过滤（40%）+ 估值/收益率双止盈，每日输出操作清单。创业板200（399019，ETF 159572）和科创50（000688，ETF 588000）分别采用对应板块的 AKShare/乐咕乐股市场 PE/PB 代理，信号会明确标注代理口径。

## 快速启动（本机，推荐）

```bash
./scripts/dev.sh
```

- 前端：http://127.0.0.1:3000（同源 `/api` 代理）
- API：http://127.0.0.1:8000/docs
- 行情仓：`backend/data/market.db`

> 本机若未安装 Docker，请用上面的脚本，不要依赖 `docker compose`。

## Docker Compose

需先安装 Docker Desktop / OrbStack / Colima。

```bash
# 生产式编排（api 无 --reload，共享行情卷）
docker compose up --build

# 开发覆盖（api hot-reload）
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

要点：

| 项 | 说明 |
|----|------|
| `market_data` volume | `api` 与 `worker` 共享 `/app/data/market.db` |
| `API_URL` build-arg | 前端 rewrite 烘焙为 `http://api:8000`（构建时注入） |
| healthcheck | `web` 等待 `api` healthy 再启动 |
| 浏览器 | 只访问 `localhost:3000`，不要访问容器内主机名 `api` |

停掉本机 `dev.sh` 占用的 3000/8000 后再起 compose，避免端口冲突。

### 测试

```bash
cd backend && pytest -q
```

## 策略与 T-1

买入仅在每周首个交易日执行；止盈每日检查。PE 使用近 5 年滚动分位，信号使用 **T-1** 收盘数据。创业板200与科创50的板块代理估值不与对应指数官方估值混称。  
`GET /api/data/status` 查看入库与月度补齐进度。

## 本地代理

```bash
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY
export NO_PROXY=localhost,127.0.0.1
./scripts/dev.sh
```

## 风险声明

策略信号仅供学习与个人研究参考，不构成投资建议。
