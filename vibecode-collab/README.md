# vibecode-collab — 联动速递（独立模块）

门户拆分批次三（2026-11-25）。collab-express.html 的后端。

- 服务：systemd unit `vibecode-collab.service`，监听 `127.0.0.1:5103`；nginx 分流 `location /api/collab/`
- 抓取：`collab_fetch.py`（微博/moxnews/漫展源）+ `collab_vision.py`（AI 读图，qwen3.6-flash，key 运行时读网关配置）
- 隐性依赖：`collab_vision.py` 尾部 import gzh_trends（公众号热词并入），故 app 内含 `gzh_trends.py` 副本——改动公众号取数逻辑时两处要同步
- 缓存：app 目录下 `.collab-cache.json` / `.collab-vision-cache.json`（7 天图片分析缓存，不入库）
- 全部接口只读，无需口令
- **漫展实时情报（2026-11-25 新增）**：`/api/collab/expo`（expo_fetch.py）——
  Google News RSS + SearXNG(强制 bing,360search,sogou) 双源取数，deepseek-v4-pro
  （token-plan 域，reasoning_effort=low）做动态查询词生成 + 漫展条目结构化抽取；
  6h 缓存、请求路径零阻塞（分钟级取数只在后台线程）；供 manga-expo.html 实时层。
  实测台账：nyato.com 已死(404)、moxnews.com 是2018年博客、bilibili show API 需签名——
  勿再试；360search/sogou 批量连查会软限流（返回0、10~40分钟自恢复），已加 0.8s 限速+20min 冷却。
  动态查询词同时反哺联动流（collab_fetch._dynamic_queries，只读缓存）。
- **科技新闻日报（2026-11-25 新增）**：`/api/collab/technews`（technews_fetch.py）——
  服务端直连 8 个 RSS 源（量子位/雷锋网/IT之家/爱范儿/钛媒体/少数派/Solidot/arXiv cs.AI），
  按标题关键词分五栏目（AI前沿/硬件数码/产业商业/开源极客/论文前沿），30min 缓存、
  请求路径零阻塞；前端 tech-news.html（替代旧 ai-news.html，后者仅留跳转壳）。
  源台账：36氪 feed 已废(0条)、虎嗅 405、量子位/Solidot 必须带 Chrome UA 否则 403。
