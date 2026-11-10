# 赚钱效应指标（insum / XY_BASE）云端跑批

通达信 `赚钱效应指标（原 INSUM）('全部A股','XY_BASE',…)` 的移植版：每个交易日收盘后，把全A股
（沪深 + 北交所，约 5580 只）逐只算一遍，按交易日聚合，产出 4 条市场级序列 +
主公式状态机，写成一个很小的 JSON。

前端页面（MACRO PULSE 的 `/breadth/`）直接读这个 JSON 画图。

## 为什么要放 GitHub Actions

本地跑批需要一台常开的机器；WorkBuddy 的定时任务在客户端关闭后就停了。
GitHub Actions 是免费的定时容器：工作日自动跑完、把 JSON 提交回本仓库，
页面从 `raw.githubusercontent.com` 直接取（该域 `Access-Control-Allow-Origin: *`，
且只缓存 5 分钟，适合每日更新）。

**不需要服务器，也不需要本地定时任务。**

## 目录

```
tools/breadth_lib.py    口径与计算库（股票池、RSI/WR、市值、聚合、主公式）
tools/breadth_run.py    全量跑批入口（并发 + 断点续跑 + 收盘就绪等待）
breadth/data/breadth.json   产出（由 Action 自动提交）
.github/workflows/breadth.yml   定时任务
```

## 口径（与需求逐条对齐，改动前请先看这里）

| 项 | 取值 |
|---|---|
| 股票池 | 全部A股 = 沪深（`m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23`）+ 北交所（`m:0+t:81+s:2048`），含 ST/科创/创业/主板/次新，剔除退市与无总市值的 |
| 复权 | `fqt=1` 前复权，单序列 |
| RSI(N) | 通达信 SMA 递推，种子 = 前 N 个 X 的算术均值 |
| WR(N) | `(HHV(H,N)-C)/(HHV(H,N)-LLV(L,N))*100`，分母为 0 记 0 |
| 市值 | 前复权 Close × 当前总股本（`f20/现价`）÷ 1e8 > 100 |
| 停牌 | 不平推，按自然交易日独立计算后按日期累加 |
| 新股 | 不足 20 根时指标为 NaN → 条件自然为 False，`COUNT` 按实际天数过渡 |

## 定时

`.github/workflows/breadth.yml`：`cron: '20 7 * * 1-5'`（15:20 北京时间，周一至周五）。

脚本内部 `wait_ready()` 会先探上证指数日K，今天那根没补上就每 5 分钟重试、
最多等 45 分钟；等不到就退出码 2、不提交（宁可当天不更新，也不写错数据）。

## 手动验证 / 补跑

Actions 页面 → Run workflow：
- `limit = 50`：只跑 50 只，用来验证「这台机器能不能连上东财」（约 10 秒）
- 留空：全量
- `no_wait = true`：跳过就绪等待，用于补跑

## 已知风险

东财（`push2his.eastmoney.com`）对机房 IP 可能限流。GitHub 的 runner 在 Azure，
如果大面积 403/超时，日志里会很明显。届时可考虑：
① 降低 `--workers`；② 换用其它行情源；③ 回到本地跑批。
