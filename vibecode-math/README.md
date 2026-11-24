# vibecode-math — 动画化解题（独立模块）

VIBE CODE 门户拆分出的第二个独立软件（2026-11-25）。
图片/文字题 AI 详解（VL+LLM）· SVG 图示 · 对话纠偏 · HyperFrames 解说动画渲染 · TTS 配音 · HTML 档案导出。

## 部署

- 代码位置：服务器 `/home/admin/apps/vibecode-math/`
- 服务：systemd unit `vibecode-math.service`（本目录有副本），监听 `127.0.0.1:5101`
- 入口：nginx 分流 `location /api/math/ → :5101`；前端 `math-solver.html`（静态目录）零改动
- 依赖：python3；ffmpeg/ffprobe（`/usr/local/bin/`，johnvansickle 静态版）；node/npx + `hyperframes@0.8.64`（版本 pin，勿随意升）；google-chrome-stable（渲染截图）
- LLM/TTS：运行时读 `~/.openclaw/openclaw.json` 的 `qwenTokenPersonalProvider`（本仓库不含任何密钥）；TTS 走 token-plan 原生 SpeechSynthesizer 口
- 写接口鉴权：`X-Footprint-Token` / body token，值读自 workspace `.secrets/footprint_token`（不入库）

## API（POST JSON，除注明外）

`/api/math/health|voices|history|anim-status|record`（读，免口令）
`/api/math/recognize|solve|chat|export|animate|delete`（写，需口令）
`GET /api/math/image?id=`（原题图回读）

## 数据（不入库，服务器上独立保存）

- `data/math_videos/`：文件型档案（index.json + <id>/record.json、project/、animation.mp4、problem.<ext>）
- 发布目录不变：`/usr/share/nginx/html/math-videos/`（成片 mp4）、`math-exports/`（自包含 HTML 存档）
- 备份由 `backup_math.sh` 覆盖（每日 3:30，OSS 异地）

## 已知行为约定

- 任务状态内存 `_tasks` + record.json 双写；服务重启后在途动画任务判 failed，前端可重试
- 档案列表只列 exported=True 的记录；未存档工作记录 7 天自动清理
- 渲染 env 需显式含 `/usr/local/bin`（ffmpeg）与 nvm bin（npx），systemd 环境 PATH 不含这些
