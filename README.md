# A股宽基定投投顾看板

个人本地投顾看板：分角色差异化定投（估值+趋势+止盈；**不含**网格/再平衡主策略，见 PRD）+ 空头排列过滤 + 估值武装追踪止盈 + 现金池弹药调节，每个交易日输出操作清单。成长仓空头排列可硬停，极端低估可小额解封；核心仓破位仅降频。创业板200 / 科创50 使用板块代理 PE+PB 复合估值（科创50 全样本分位）。

## 快速启动（本机，推荐）

```bash
./scripts/dev.sh
```

- 前端：http://127.0.0.1:3000（同源 `/api` 代理）
- API：http://127.0.0.1:8000/docs
- 行情仓：`backend/data/market.db`（价格与估值分别检查新鲜度；价格已到 T-1 但估值过期时仍会刷新估值；`force=true` 强制全量）
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

`base_amount` 为**月定投总预算**；可在设置中选每日/每周/每月执行日，系统按 XSHG 官方完整当月交易日历折算本期分配额度，日历不可用时暂停分配。页面额度用于策略预算分配，不等于按 ETF 一手规则取整后的券商可下单金额。硬否决与倍数按标的 profile 分化；止盈为估值武装 + 峰值回撤。信号使用 **T-1** 收盘数据。
估值 `5y` 分位按信号日回溯 5 个自然年；估值滞后超过 5 个 XSHG 交易日或样本不足时按数据缺失安全暂停新增。
合成倍数采用加权平均；当 profile 的 `scale_to_cap=true`（默认）时，按角色可达上限（估值最高档 × 估值权重 + 多头趋势 × 趋势权重）等比放大，使配置的 `max_mult`（默认 2.0）在「低估 + 多头」时真正可达，`normalize_buy_cap` 预算上限因此成为有效的安全阀。
默认权重：HS300 35% / ZZ500 25% / CYB200 15% / KCB50 10% / SZ50 15%。  
`GET /api/data/status` 查看入库与月度补齐进度。

设置保存会校验预算、现金、份额、成本、预算上限、均线周期、止盈分位和目标权重；目标权重只接受上述标的且合计必须为 1。现金池默认关闭；启用后现金余额是实值，**`0` 表示弹药为空，pool_factor 归 0、本期暂停买入**（不再是 0.35× 下限），满额基准为 `base_amount × 36`。**成长仓空头策略**可在设置页切换：防守（空头排列硬停）或追收益（空头软降频小额续投，倍数可调，默认 0.2×）——回测显示这是当前策略最大的收益/防守取舍旋钮。

## 对账 · 复盘 · 提醒（v5）

- **对账登记**：设置页「对账登记」可登记真实执行的入金 / 买入 / 卖出 / 分红，自动演进份额、加权成本、现金与累计分红并写入台账；盈亏采用含分红的**总回报口径**（市值 + 累计分红 − 成本），消除分红除权造成的盈亏失真。
- **信号复盘**：`/review` 页按时间线回看每个历史信号日的决策，以及之后 5 / 20 / 60 日与至今的等权前瞻收益（基于各标的指数 T-1 收盘）。
- **提醒推送**：设置页填写 Webhook 地址（Server酱 / 钉钉 / 企业微信），后台 worker 在执行日有买入 / 减仓时推送信号；可再开启「信号相对昨日变化」提醒。

## 离线回测

回测**复用实盘策略管线**（`app/services/strategy_pipeline.py`）：与看板相同的估值新鲜度 fail-safe、硬否决、止盈锁与底仓逻辑，信号取 T-1 并在下一交易日执行：

```bash
PYTHONPATH=backend backend/.venv/bin/python backend/scripts/backtest_strategy.py \
  --start 2020-01-01 --frequency monthly --monthly-cashflow 10000
```

输出策略与等权固定定投的 XIRR、TWR、最大回撤、波动率、暂停率、成本敏感性和年度摘要。更多检查：

```bash
# 样本外分割（含 2024-01-01 起）；样本外 XIRR 计入期初已投入资本
... --oos-start 2024-01-01

# 单因子敏感性扫描（每次仅动一个旋钮）
... --sensitivity

# 成长仓空头策略（与设置页同一开关）：hard_veto=防守 / soft=追收益软降频
... --growth-bear-policy soft
```

实测提示：全样本下估值+趋势择时的策略收益低于等权固定定投（现金拖累），换取更低的波动与回撤；**成长仓空头硬否决是本策略最大的收益拖累**（软降频变体 XIRR 3.25%→4.51%、TWR 20.3%→25.5%）。任何参数选择都需要样本外证据支撑；回测受样本期、代理估值与 ETF 上市日期限制，历史结果不构成投资建议。

## 本地代理

```bash
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY
export NO_PROXY=localhost,127.0.0.1
./scripts/dev.sh
```

## 风险声明

策略信号仅供学习与个人研究参考，不构成投资建议。
