# VIBE CODE 模块中心

个人工具集门户 —— 一个自建的轻量级模块化站点，涵盖信息聚合、数据分析与工艺管理三大类工具。
纯静态页面 + Python 后端，部署在单台轻量云服务器上，由 nginx 对外服务。

> ⚠️ **本仓库为公开仓库**。请勿提交任何含个人隐私、公司业务数据或凭据的文件。
> 当前 `.gitignore` 已排除工艺数据库（`routing.db`）、运行时数据（`.routing-data/`）与本地备份包。

---

## 目录结构

```
.
├── src/                    # 源代码（后端脚本 + 页面源文件）
│   ├── server.py           # 主后端：工艺路线数据 API + 股票报告服务（端口 8765）
│   ├── generate_stock_report.py   # AI 股票分析报告生成器
│   ├── collab_fetch.py     # 二次元联动 / 漫展情报抓取
│   ├── lockup_radar.py     # A股限售解禁数据抓取
│   ├── sentiment_daily.py  # 市场情绪日报生成
│   ├── *.html              # 各模块页面源文件
│   └── stock-sentiment/    # 股票舆情分析子系统（Flask，端口 5000）
│       ├── app.py
│       ├── crawlers/       # 数据源：雪球 / 东方财富
│       ├── analysis/       # 情绪分析（jieba 分词 + 金融词典）
│       ├── templates/      # 页面模板
│       └── static/         # 静态资源
├── deployed/               # nginx 实际对外服务的页面与报告
│   └── stock-reports/      # 已生成的分析报告（HTML / PDF）
├── nginx/                  # nginx 配置（异地还原用）
└── docs/                   # 备份元信息
```

## 功能模块

### 信息聚合

| 页面 | 说明 |
|---|---|
| `index.html` | 模块中心主页 |
| `ai-news.html` | AI 新闻日报 Top 10 |
| `hot-search.html` | 全网热搜榜 |
| `collab-express.html` | 二次元联动速递 |
| `manga-expo.html` | 漫展情报局 |

### 时间与工具

| 页面 | 说明 |
|---|---|
| `clock.html` | 多风格时钟 |
| `world-clock.html` | 世界时钟（含时区地图） |

### 金融数据

| 页面 | 说明 |
|---|---|
| `lockup-radar.html` | A股限售解禁预警雷达 |
| `sentiment-daily.html` | A股市场情绪日报 |
| `stock-daily.html` | 股市日报 |
| `stock-reports.html` | AI 股票分析报告 |

### 工艺管理

| 页面 | 说明 |
|---|---|
| `process-route.html` | 工艺路线图生成器 |
| `process-template.html` | 工艺路线模板 |
| `routing.html` | 工艺路线模板 |

## 技术栈

- **前端**：原生 HTML / CSS / JavaScript，无构建步骤
- **后端**：Python 3（`http.server` 与 Flask 双服务）
- **数据**：SQLite（`routing.db`，不纳入版本管理）
- **依赖**：`flask` `requests` `beautifulsoup4` `jieba` `openpyxl` `Pillow` 等
- **部署**：nginx 反向代理 + 静态托管

## 服务端口约定

| 服务 | 端口 | 说明 |
|---|---|---|
| `server.py` | 8765 | 工艺路线 API、股票报告生成 |
| `stock-sentiment/app.py` | 5000 | 舆情分析 |

nginx 将 `/api/` 代理至 8765、`/sentiment/` 代理至 5000，配置见 `nginx/`。

## 本地运行

```bash
# 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS

# 安装依赖
pip install flask requests beautifulsoup4 jieba openpyxl

# 启动主服务
python src/server.py
```

访问 `src/index.html` 预览页面，或通过本地 HTTP 服务托管 `deployed/`。

## 部署要点

服务器上的目录映射（详见 `nginx/` 配置）：

- 页面根目录 → `deployed/`
- 报告发布目录 → `/usr/share/nginx/html/stock-reports`
- 后端反代 → `127.0.0.1:8765`（`/api/`）、`127.0.0.1:5000`（`/sentiment/`）

## 数据备份说明

运行时数据全部**不入库**，仅存服务器本地并由 OSS 备份覆盖：

- `routing.db` —— 全站通用 KV（门户卡片布局、股票收藏等；工艺路线功能已于 2026-11-24 下线，表数据保留）
- `apps/vibecode-bazi/data/`（排盘记录+报告）、`apps/vibecode-gzh/data/`（关键词表）、`apps/vibecode-math/data/`（解题档案）
- `aigen.db`、足迹照片 `footprint-images/`、漫展/联动缓存等

备份脚本：`backup_vibe.sh`（每周全量→OSS，含 workspace + apps 源码 + nginx + systemd 单元）、`backup_math.sh`（每日 3:30，解题档案）、`aigen-backup.sh` / `aigen-image-backup.sh`（每日，生成库）。

---

*最后更新：2026-11-25*

## 模块化拆分（2026-11-25 起）

按"每个软件独立"原则，各模块逐步拆为 `vibecode-<模块>/` 子目录（独立代码+部署单元，数据留服务器本地不入库）：

- `vibecode-bazi/` — 八字分析独立服务（:5100，nginx `/api/bazi/` 分流）✅
- `vibecode-math/` — 动画化解题独立服务（:5101，nginx `/api/math/` 分流，依赖 ffmpeg + hyperframes@0.8.64）✅
- `vibecode-gzh/` — 公众号热门文章（:5102，nginx `/api/gzh/` 分流）✅
- `vibecode-collab/` — 联动速递（:5103，nginx `/api/collab/` 分流）✅
- `vibecode-lockup/` — 解禁预警雷达（:5104，nginx `/api/lockup/` 分流）✅
- `vibecode-sentiment/` — 市场情绪日报（:5105，nginx `/api/sentiment/` 分流）✅
- 工艺路线模块已下线：删除 `src/`、`deployed/` 中 routing/process 页面（2026-11-24）；AI姿势参考图库已下线（2026-11-25）
- 服务器端：单元文件在 `/etc/systemd/system/vibecode-*.service`，代码在 `/home/admin/apps/vibecode-*/`；`src/`、`deployed/` 保留作 10-15 单体快照，新改动一律进对应 vibecode-* 子目录
