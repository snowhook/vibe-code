# vibecode-bazi — 八字分析（独立模块）

VIBE CODE 门户拆分出的第一个独立软件（2026-11-25 试点）。
四柱排盘 · 旺衰/喜用神 · AI 详批命书 · PDF 报告 · 历史记录。

## 部署

- 代码位置：服务器 `/home/admin/apps/vibecode-bazi/`
- 服务：systemd unit `vibecode-bazi.service`（本目录有副本），监听 `127.0.0.1:5100`
- 入口：nginx 分流 `location /api/bazi/ → :5100`；前端 `bazi.html`（静态目录）零改动
- 依赖：python3 + pytz + weasyprint（PDF）；排盘内核复用 `skills/bazi-expert/`（bazi_core）
- LLM：运行时读 `~/.openclaw/openclaw.json` 的 `qwenTokenPersonalProvider`（本仓库不含任何密钥）
- 写接口鉴权：`X-Footprint-Token` / body token，值读自 workspace `.secrets/footprint_token`（不入库）

## API（POST，JSON）

`/api/bazi/health|cities|lunar-days|focus-keywords|focus-keywords/save|analyze|report|report/html|report/simple|report/simple-pdf|pdf|records|record|record/delete`

写操作需口令；报告 PDF 发布到 `/usr/share/nginx/html/bazi-reports/`。

## 数据（不入库，服务器上独立保存）

`data/bazi.db`（bazi_records 历史排盘 + kv 关注词）、`data/bazi_reports/`（报告产物）。
由每日/每周备份脚本覆盖。
