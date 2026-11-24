# vibecode-sentiment — 市场情绪日报（独立模块）

门户拆分批次三（2026-11-25）。sentiment-daily.html 的后端。

- 服务：systemd unit `vibecode-sentiment.service`，监听 `127.0.0.1:5105`；nginx 分流 `location /api/sentiment/`
- `sentiment_daily.py` 东财龙虎榜/行业/概念板块按日构建，无状态、无本地数据
