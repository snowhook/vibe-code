# vibecode-lockup — 解禁预警雷达（独立模块）

门户拆分批次三（2026-11-25）。lockup-radar.html 的后端。

- 服务：systemd unit `vibecode-lockup.service`，监听 `127.0.0.1:5104`；nginx 分流 `location /api/lockup/`
- `lockup_radar.py` 东方财富解禁数据实时取数，无状态、无本地数据
