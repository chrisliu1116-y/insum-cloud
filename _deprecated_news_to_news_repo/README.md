# 事件卡/快讯/radar 历史副本（已弃用）

> 2026-11-22 归档：这些文件原属"事件卡/快讯/Polymarket雷达"链路。
> 该链路早在 v455 已整体迁至 **macropulse-news 仓库**，本目录仅为历史留档。
>
> **权威实现**：事件卡提纯 `macropulse-news/tools/evolve_events.py`、
> 新卡 `event_incubator.py`、雷达 `polymarket_radar.py`、写卡 `radar_publish.py`、
> LLM 通道 `llm_pool.py`。
>
> **本仓库（insum-cloud）现在只负责赚钱效应跑批**（breadth.yml / proxy-live.yml / watchdog 等）。
> 对应的两个残留 workflow 已加 `.disabled` 后缀（GHA 不再加载）。
>
> 勿在此目录修改或复用代码——改动不会生效。GEMINI/ZHIPU key 也应配在 macropulse-news 仓库。
