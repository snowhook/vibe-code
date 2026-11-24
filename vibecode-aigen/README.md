# vibecode-aigen — AI人物生成平台数据后端 + 生图同源代理（独立模块）

门户拆分批次（2026-11-25）。`/aigen/` 前端站点的完整后端。

## 部署

- 代码位置：服务器 `/home/admin/apps/vibecode-aigen/`
- 服务：systemd unit `vibecode-aigen.service`（本目录有副本），监听 `127.0.0.1:5106`
- nginx 分流：`location /api/aigen`、`location = /api/proxy`、`location /api/aikit/` → :5106
- 三条链路（逐条从 server.py 单体复刻，含全部历史修复）：
  - `/api/aigen/*`：统一数据后端（entries/vault/records/prompts/docs/characters/projects），乐观并发 ver，写需口令
  - `/api/proxy`：生图同源代理——上游白名单（ark/dashscope/token-plan/kling/minimax/gemini/openai/deepseek + `*.maas.aliyuncs.com`/`*.qianwenaiapi.com` 后缀）、40MiB body 上限、token-plan key 服务端代持注入（浏览器零暴露）、ark b64→url 改写、大 body 走 curl 转发（python ssl 对北京大 body 会 SSLEOFError）、三家结果图抓回 `/pose-out/` 换同源 URL
  - `/api/aikit/*`：AI 接口工具箱（列模型/测连通），主机白名单 + 每 IP 限速 30/60s
- LLM key：运行时读 `~/.openclaw/openclaw.json` 的 `qwenTokenPersonalProvider`（按 mtime 缓存），仓库不含密钥
- 口令：读 workspace `.secrets/footprint_token`（共享门户口令，不入库）

## 数据（不入库）

- `aigen.db`（拆分时自 workspace 一致性复制，行数对账 4771/54/21/25/6）
- `ROUTING_DB` 固定指向 workspace 共享 routing.db，仅供一次性 import-kv/reconcile
- 图片仍在 `/usr/share/nginx/html/{footprint-images,pose-out}/`（nginx 静态托管，未迁移）
- 备份：`aigen-backup.sh`（每日）与 `aigen-image-backup.sh`（每周 OSS）已改指本 app 的 aigen.db
