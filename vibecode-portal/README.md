# vibecode-portal — 门户核心（独立模块）

2026-11-25 全模块拆分完成后的 `server.py` 本体（568 行，原 1914 行）：
**门户 KV 存储 + 卡片管理 + 足迹照片 + 静态站点托管**。这是最后的地基，保留在 :8765。

## 保留接口

- `/api/save|load|delete|keys`：通用 KV（routing.db；门户卡片 `portal_modules`、股票收藏等都存在这里）
- `/api/portal/{list,add,update,delete,move}`：门户卡片原子操作（写需口令）
- `/api/footprint/{upload,imgdel}`：足迹照片落 nginx 静态目录，内联生成 `.t800.webp` 缩略图（已解除对 aigen_store 的依赖）
- 静态文件服务（workspace 目录），供 nginx 未接管的本地调试

## 部署

- 代码位置：服务器 `~/.openclaw/workspace/server.py`（位置不变，systemd `routing-server.service`）
- 口令文件：`~/.openclaw/workspace/.secrets/footprint_token`（env FOOTPRINT_TOKEN 优先）
- 备份：本目录四个脚本 = 门户全量周备（含 apps/ 与 nginx/systemd 配置快照）+ 解题档案日备 + 生成库日备/周备 + pose-out 清理

## 拆分后的模块地图（同仓库兄弟目录）

| 子目录 | 端口 | 职责 |
|--------|------|------|
| vibecode-bazi | :5100 | 八字分析（排盘/AI详批/PDF） |
| vibecode-math | :5101 | 动画化解题（VL解题/HyperFrames渲染/TTS） |
| vibecode-gzh | :5102 | 公众号热门文章 |
| vibecode-collab | :5103 | 联动速递（含 AI 读图） |
| vibecode-lockup | :5104 | 解禁预警雷达 |
| vibecode-sentiment | :5105 | 市场情绪日报 |
| vibecode-aigen | :5106 | 生成库数据后端 + 生图代理 + aikit |
| vibecode-stockreports | :5107 | 股票分析报告 + 东财快讯 |
| （仓库内）lottery/ | :5010 | 彩票选号账本（独立 systemd） |
| （仓库内）src/stock-sentiment/ | :5000 | 股票舆情分析（独立 systemd） |
