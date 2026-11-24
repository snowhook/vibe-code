# vibecode-gzh — 公众号热门文章（独立模块）

门户拆分批次三（2026-11-25）。gzh-trending.html / wechat-hot.html 的后端。

- 服务：systemd unit `vibecode-gzh.service`，监听 `127.0.0.1:5102`；nginx 分流 `location /api/gzh/`
- 抓取：`gzh_trends.py` 搜狗微信匿名抓取（0.4s 限速 + 10 分钟结果缓存），无密钥依赖
- 关键词表：`data/gzh.db` kv（2026-11-25 从 routing.db 的 gzh_keywords 迁移，不入库）
- `keywords/save` 写接口需 X-Footprint-Token；其余读接口开放
