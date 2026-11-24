# vibecode-collab — 联动速递（独立模块）

门户拆分批次三（2026-11-25）。collab-express.html 的后端。

- 服务：systemd unit `vibecode-collab.service`，监听 `127.0.0.1:5103`；nginx 分流 `location /api/collab/`
- 抓取：`collab_fetch.py`（微博/moxnews/漫展源）+ `collab_vision.py`（AI 读图，qwen3.6-flash，key 运行时读网关配置）
- 缓存：app 目录下 `.collab-cache.json` / `.collab-vision-cache.json`（7 天图片分析缓存，不入库）
- 全部接口只读，无需口令
