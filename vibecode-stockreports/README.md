# vibecode-stockreports — AI 股票分析报告（独立模块）

门户拆分批次（2026-11-25）。stock-reports.html 的后端，顺带承接股市日报的东财快讯代理。

## 部署

- 代码位置：服务器 `/home/admin/apps/vibecode-stockreports/`
- 服务：systemd unit `vibecode-stockreports.service`（本目录有副本），监听 `127.0.0.1:5107`
- nginx 分流：`location /api/reports/`、`location /api/news/` → :5107
- 接口：`/api/reports/{list,generate,delete,pdf}`（generate/delete/pdf 需口令）+ `/api/news/data`（读，东财 7x24 快讯 A/港/美分块，60s 缓存）
- 生成链：subprocess 调 `generate_stock_report.py`（DCF 价值投资分析，stdout 回 JSON 元信息）；PDF 用 weasyprint（带 mtime 缓存）
- 发布目录不变：`/usr/share/nginx/html/stock-reports/`（生成/删除双向同步）

## 数据（不入库）

- `data/stockreports.db`（kv: stock_reports 台账，拆分时自 routing.db 迁移）
- `data/stock-reports/`（HTML/PDF 产物，14 文件已迁移对账）
