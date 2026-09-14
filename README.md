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

**`routing.db` 是整个系统最有价值的资产** —— 工艺路线记录、模板、工序/风险库全部存于此。
该文件已被 `.gitignore` 排除，**不会进入本仓库**，需单独离线备份。

页面改动频率低，数据变化基本都集中在这个数据库中，因此增量备份只需更新该文件。

---

*最后更新：2026-09-14*
