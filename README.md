# A股宽基定投投顾看板

个人本地投顾看板：分角色差异化定投（核心估值70%/趋势30%，成长55%/45%）+ 空头排列过滤 + 估值武装追踪止盈 + 现金池弹药调节，每个交易日输出操作清单。成长仓空头排列可硬停，极端低估可小额解封；核心仓破位仅降频。创业板200 / 科创50 使用板块代理 PE+PB 复合估值（科创50 全样本分位）。

## 快速启动（本机，推荐）

```bash
./scripts/dev.sh
```

- 前端：http://127.0.0.1:3000（同源 `/api` 代理）
- API：http://127.0.0.1:8000/docs
- 行情仓：`backend/data/market.db`
- 持仓/设置：`backend/data/user_state.json`（本机 `MONGODB_URI=memory` 时也会落盘，重启不丢）

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

买入每个交易日可执行；硬否决与倍数按标的 profile 分化；止盈为估值武装 + 峰值回撤。信号使用 **T-1** 收盘数据。代理估值不与官方指数估值混称。  
默认权重：HS300 35% / ZZ500 25% / CYB200 15% / KCB50 10% / SZ50 15%。  
`GET /api/data/status` 查看入库与月度补齐进度。

## 本地代理

```bash
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY
export NO_PROXY=localhost,127.0.0.1
./scripts/dev.sh
```

## 风险声明

策略信号仅供学习与个人研究参考，不构成投资建议。
