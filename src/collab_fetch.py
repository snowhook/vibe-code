#!/usr/bin/env python3
"""二次元联动速递 - 实时情报抓取
通过本机 SearXNG (localhost:8080) 聚合搜索最新品牌×IP联动新闻。
被 server.py 的 /api/collab/fetch 调用。
"""
import json
import os
import re
import time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

SEARXNG = 'http://localhost:8080/search'
GNEWS = 'https://news.google.com/rss/search'
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.collab-cache.json')

# 搜索矩阵：单品牌关键词命中率更高（多品牌组合查询常空）
QUERIES = [
    '喜茶 联名',
    '奈雪的茶 联名',
    '古茗 联名 动漫',
    '茶百道 联名',
    '霸王茶姬 联名',
    '瑞幸 联名 二次元',
    '库迪 联名',
    '星巴克 联名 IP',
    '肯德基 联名 二次元',
    '麦当劳 联名 动漫',
    '优衣库 联名 动漫',
]  # 原版非精确短语，SearXNG 可返回带 content 摘要的中文站结果

# Google News RSS 查询（推荐：自带发布日期，分词精准）
# 用双引号锁短语 + 关键词，避免 bing 单字分词噪音
GNEWS_QUERIES = [
    '"联名" 动漫 茶饮',
    '"联名" 动漫 咖啡',
    '"联名" 手游',
    '"联动" 奶茶',
    '瑞幸 联名',
    '喜茶 联名',
    '古茗 联动',
    '霸王茶姬 联名',
    '肯德基 联动',
    '优衣库 联名 动漫',
]

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 collab-radar/1.0'

KEYWORDS = ('联名', '联动', '聯名', '聯動', 'コラボ')
BRAND_HINTS = ('喜茶', '奈雪', '古茗', '茶百道', '霸王茶姬', '瑞幸', '库迪', '星巴克', '蜜雪', 'CoCo', '都可',
               '益禾堂', '沪上阿姨', '茉莉奶白', '书亦', '一点点', '柠季', '甜啦啦', '肯德基', 'KFC', '麦当劳',
               '必胜客', '汉堡王', '德克士', '塔斯汀', '华莱士', '海底捞', '寿司郎', '摩斯汉堡', '优衣库',
               'UNIQLO', '优酸乳', '洽洽', '奶茶', '茶饮', '茶飲', '咖啡')
NOISE = ('缤果', '五菱', '电动车', '雅迪')  # 非餐饮/时尚×IP联动噪音
TIME_RANGE = 'month'  # 只抓近一个月

# 备用源：SearXNG 引擎限流时直接抓这些页面（标题里含联名的链接）
FALLBACK_SOURCES = [
    ('https://news.17173.com/', '17173新闻'),
    ('https://www.dongman.la', '动漫啦'),
]
FALLBACK_LINK_RE = re.compile(
    r'<a[^>]+href="([^"]+)"[^>]*>([^<]{8,80})</a>', re.I)


def _fetch_fallback():
    """SearXNG 失败时的兜底：直接抓新闻列表页，筛出含“联名/联动”标题的条目"""
    out = []
    for url, src in FALLBACK_SOURCES:
        try:
            req = urllib.request.Request(url, headers={'User-Agent':
                'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 collab-radar/1.0'})
            with urllib.request.urlopen(req, timeout=12) as r:
                html = r.read().decode('utf-8', 'ignore')
            for href, title in FALLBACK_LINK_RE.findall(html):
                title = title.strip()
                if not any(k in title for k in KEYWORDS):
                    continue
                if any(k in title for k in NOISE):
                    continue
                if not any(b in title for b in BRAND_HINTS):
                    continue
                full = href if href.startswith('http') else urllib.parse.urljoin(url, href)
                out.append({'title': title, 'url': full, 'snippet': src,
                            'time': '', 'q': src, 'score': 5})
        except Exception:
            continue
    return out


def _fetch_one(q):
    # 引擎限流时 time_range 过滤常常空结果，先试 month，空则降级为不带时间过滤
    for tr in (TIME_RANGE, ''):
        try:
            items = _search(q, tr)
        except Exception:
            items = []
        if items:
            return items
    return []


def _gnews(q, days=14):
    """Google News RSS 搜索：自带发布日期，分词精准
    返回 [{title,url,snippet,time(YYYY-MM-DD),q,score}]，按时倒序
    """
    from email.utils import parsedate_to_datetime
    import datetime as _dt
    params = {'q': q, 'hl': 'zh-CN', 'gl': 'CN', 'ceid': 'CN:zh-Hans'}
    url = GNEWS + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as r:
        xml = r.read().decode('utf-8', 'ignore')
    out = []
    now = _dt.datetime.now(_dt.timezone.utc)
    for it in re.findall(r'<item>(.*?)</item>', xml, re.S):
        tm = re.search(r'<title>([^<]*)</title>', it)
        lk = re.search(r'<link>([^<]*)</link>', it)
        pd = re.search(r'<pubDate>([^<]*)</pubDate>', it)
        dc = re.search(r'<description>(.*?)</description>', it, re.S)
        title = (tm.group(1).strip() if tm else '').strip()
        url = (lk.group(1).strip() if lk else '').strip()
        if not title or not url:
            continue
        if not any(k in title for k in KEYWORDS):
            continue
        if any(k in title for k in NOISE):
            continue
        pub = ''
        if pd:
            try:
                ts = parsedate_to_datetime(pd.group(1).strip())
                pub = ts.strftime('%Y-%m-%d')
                if (now - ts).days > days:
                    continue
            except Exception:
                pass
        snippet = ''
        if dc:
            import html as _html
            raw_desc = _html.unescape(dc.group(1))   # &lt;a → <a
            mf = re.search(r'<font[^>]*>(.*?)</font>', raw_desc, re.S)
            if mf:
                src = re.sub(r'<[^>]+>', '', mf.group(1)).strip()
                if src:
                    snippet = '来源：' + src
            if not snippet:
                s2 = re.sub(r'<[^>]+>', '', raw_desc)
                s2 = re.sub(r'https?://\S+', '', s2)
                s2 = re.sub(r'\s+', ' ', s2).strip()[:200]
                if s2:
                    snippet = s2
        out.append({'title': title, 'url': url, 'snippet': snippet,
                    'time': pub, 'q': q, 'score': 2})
    return out


def _fetch_gnews():
    """Google News 聚合：并发多关键词，去重，按时倒序"""
    all_items = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_gnews, q): q for q in GNEWS_QUERIES}
        for f in as_completed(futs):
            try:
                for it in f.result():
                    key = it['url'].split('?')[0].rstrip('/')
                    tkey = ''.join(it['title'].split())[:40]
                    if key in all_items:
                        continue
                    if any(''.join(v['title'].split())[:40] == tkey for v in all_items.values()):
                        continue
                    all_items[key] = it
            except Exception:
                continue
    items = list(all_items.values())
    items.sort(key=lambda x: x['time'] or '0000-00-00', reverse=True)
    return items


def _search(q, time_range):
    params = {'q': q, 'format': 'json', 'language': 'zh-CN', 'safesearch': 0}
    if time_range:
        params['time_range'] = time_range
    req = urllib.request.Request(
        SEARXNG + '?' + urllib.parse.urlencode(params),
        headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) collab-radar/1.0'})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode('utf-8'))
    out = []
    for item in data.get('results', [])[:8]:
        title = (item.get('title') or '').strip()
        snippet = (item.get('content') or '').strip()
        url = item.get('url') or ''
        blob = title + snippet
        if not url or not title:
            continue
        if not any(k in blob for k in KEYWORDS):
            continue
        if any(k in blob for k in NOISE):
            continue
        pub = item.get('publishedDate') or ''
        out.append({'title': title, 'url': url, 'snippet': snippet[:220],
                    'time': pub[:10] if pub else '', 'q': q,
                    'score': item.get('score', 99)})
    return out


def structured_collabs(max_items=24):
    """主区精选联动：回到原数据源 SearXNG，从 content 提取丰富描述，过滤百科噪音"""
    out, seen = [], set()
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_fetch_one, q): q for q in QUERIES}
        for f in as_completed(futs):
            try:
                for it in f.result():
                    title = (it.get('title') or '').strip()
                    if not title or title in seen:
                        continue
                    seen.add(title)
                    blob = title + ' ' + (it.get('content') or '')
                    # 过滤百科/词典/无关噪音
                    if any(k in blob for k in ('百科','拼音','汉字','词典','汉语','户口','驾驶证','贷款','游戏下载','官网')):
                        continue
                    if not any(k in blob for k in KEYWORDS):
                        continue
                    if any(k in blob for k in NOISE):
                        continue
                    core = re.split(r'\s*[-|—]\s*', title)[0][:28]
                    # 品牌
                    brand = next((b for b in BRAND_HINTS if b and b in core), core[:5])
                    # 分类
                    cat = 'tea'
                    if any(k in core for k in ('肯德基','麦当劳','必胜客','汉堡','餐')): cat='food'
                    elif any(k in core for k in ('优衣库','UNIQLO','服装','服饰')): cat='fash'
                    elif any(k in core for k in ('游戏','手游','电竞')): cat='game'
                    em = {'tea':'🧋','food':'🍔','fash':'👕','game':'🎮'}.get(cat,'📡')
                    # 描述（用 SearXNG 的 content 正文，这才是原数据源的丰富字段）
                    desc = (it.get('content') or '').strip()[:160] or core
                    out.append({
                        'brand':brand,'em':em,'cat':cat,'ip':'联动企划','ipType':'game' if cat=='game' else 'char',
                        'title':core,'s':'','e':'','desc':desc,'perks':[],'region':'','hot':'',
                        'url':it.get('url',''),'_live':True,
                    })
                    if len(out)>=max_items: break
            except Exception:
                continue
            if len(out)>=max_items: break
    return {'items': out, 'fetched_at': time.strftime('%Y-%m-%d %H:%M:%S')}


def fetch_all(max_items=40):
    t0 = time.time()
    used_fallback = False
    sources_used = set()
    # 情报流主源：恢复 SearXNG（原数据源，返回中文站点 title+content 摘要）
    # Google News 的 description 是自引用链接而非正文摘要，混入会让情报流变成链接堆——故剔除，
    # 仅在 SearXNG 结果不足时用 Google News 补数量。
    items2 = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_fetch_one, q): q for q in QUERIES}
        for f in as_completed(futs):
            try:
                for it in f.result():
                    it['src'] = 'searxng'
                    key = it['url'].split('?')[0].rstrip('/')
                    tkey = ''.join(it['title'].split())[:40]
                    if key in items2:
                        if it['score'] < items2[key]['score']:
                            items2[key] = it
                        continue
                    if any(''.join(v['title'].split())[:40] == tkey for v in items2.values()):
                        continue
                    items2[key] = it
            except Exception:
                continue
    live = list(items2.values())
    sources_used.add('searxng') if live else None

    # SearXNG 结果不足时，用 Google News 补充（仅当缺口较大）
    if len(live) < 10:
        g = _fetch_gnews()
        for it in g:
            it['src'] = 'google'
        seen_keys = {x['url'].split('?')[0].rstrip('/') for x in live}
        seen_tk = {''.join(x['title'].split())[:40] for x in live}
        for c in g:
            ck = c['url'].split('?')[0].rstrip('/')
            ct = ''.join(c['title'].split())[:40]
            if ck in seen_keys or ct in seen_tk:
                continue
            live.append(c)
            seen_keys.add(ck); seen_tk.add(ct)
            sources_used.add('google')

    # 仍为空 → 备用源兜底
    if not live:
        live = _fetch_fallback()
        for it in live:
            it['src'] = 'fallback'
        used_fallback = bool(live)
        if live:
            sources_used.add('fallback')

    new_count = len(live)

    # 与缓存合并：新抓的排前面，同标题去重，保证列表始终有足够情报
    cached = []
    try:
        with open(CACHE_PATH, encoding='utf-8') as fp:
            cached = json.load(fp).get('items', [])
    except Exception:
        pass
    seen_titles = set(''.join(x['title'].split())[:40] for x in live)
    seen_urls = set(x['url'].split('?')[0].rstrip('/') for x in live)
    for c in cached:
        tkey = ''.join(c['title'].split())[:40]
        ckey = c['url'].split('?')[0].rstrip('/')
        if tkey in seen_titles or ckey in seen_urls:
            continue
        live.append(c)
        seen_titles.add(tkey)
        seen_urls.add(ckey)

    def rank(x):
        blob = x['title'] + x.get('snippet', '')
        brand_hit = 1 if any(b in blob for b in BRAND_HINTS) else 0
        fresh = 1 if x.get('time', '') else 0
        return (brand_hit, fresh, x.get('time', ''))
    live.sort(key=rank, reverse=True)
    arr = live[:max_items]

    result = {'ok': True, 'items': arr, 'fetched_at': time.strftime('%Y-%m-%d %H:%M:%S'),
              'cost_ms': int((time.time() - t0) * 1000), 'queries': len(QUERIES) + len(GNEWS_QUERIES),
              'sources': sorted(sources_used) if sources_used else ['searxng'],
              'total_found': new_count, 'from_cache': False,
              'from_fallback': used_fallback}
    if arr:
        try:
            with open(CACHE_PATH, 'w', encoding='utf-8') as fp:
                json.dump(result, fp, ensure_ascii=False)
        except Exception:
            pass
    else:
        result['ok'] = False
        result['error'] = '搜索引擎暂时限流，且无本地缓存，请稍后再试'
    return result
