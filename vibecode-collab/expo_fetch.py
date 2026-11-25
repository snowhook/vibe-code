#!/usr/bin/env python3
"""漫展实时情报 + DeepSeek 搜索增强 —— vibecode-collab 子模块（2026-11-25 新增）

背景：manga-expo.html 原为纯静态页（nyato 数据手工导出，站点已死，285 条全部停更）。
本模块提供 /api/collab/expo：SearXNG(强制 360search+sogou 引擎) + Google News RSS
双源取数 → deepseek-v4-pro 结构化抽取「漫展定档/时间表/开票」→ 6h 缓存，
前端把实时条目并入静态清单并加 🛰️ 实时 标识。

源实测台账（2026-11-25）：
- SearXNG 默认引擎集文本搜索 0 结果（google空/baidu解析错/ddg验证码）——必须带
  engines=360search,sogou；这两路稳定出中文站 title+content（content 常带日期前缀）。
- Google News RSS 可达（title 含定档信息密度尚可，正文是 JS 壳抓不到）。
- 已死源勿再试：nyato.com（404）、moxnews.com（2018年 Cannabis 博客，非漫展站）、
  bilibili show API（81102084 需签名）。bendibao.com 正文静态可抓（排期表富矿）。
- DeepSeek：官方 API 无原生联网搜索；deepseek-v4-pro 走 token-plan 域做
  「动态查询词生成 + 情报抽取」层。实测坑：默认思考模式吃光 max_tokens → content 空，
  必须 reasoning_effort='low'（或 enable_thinking=False）；传输用 http.client（urllib
  会被 token-plan 间歇拦截）。key 读 openclaw.json，绝不打印/落日志。
"""
import datetime as _dt
import http.client
import json
import os
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

APP = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(APP, '.expo-cache.json')
QPATH = os.path.join(APP, '.collab-queries.json')
CONFIG_PATH = os.path.expanduser('~/.openclaw/openclaw.json')
DS_MODEL = 'deepseek-v4-pro'
TTL_EXPO = 6 * 3600
TTL_Q = 24 * 3600

SEARXNG = 'http://localhost:8080/search'
SEARX_ENGINES = 'bing,360search,sogou'
GNEWS = 'https://news.google.com/rss/search'

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

EXPO_QUERIES = ['漫展 时间表', '漫展 定档', '漫展 12月', '2027元旦 漫展 汇总',
                '漫展 开票 2026', '漫展 春节 2027', 'CP漫展', '萤火虫漫展', 'CICF', '动漫节']
EXPO_KW = ('漫展', '动漫展', '动漫节', '游戏展', '同人展', '嘉年', 'ACG', 'CICF',
           'COMICUP', 'CP展', 'IDO', '萤火虫', '潮玩', '展览', 'EXPO', '谷子',
           '电玩展', '祭', '动漫嘉年华')
LIST_RE = re.compile(r'(时间表|档期|开票|汇总|攻略|日历|售票|定档|排期|开幕)')
# 正文可静态抓取的排期富矿域（其余域名正文是 JS 壳，只吃 title）
ARTICLE_HOSTS = ('bendibao.com', 'jufair.com')
DATE_TOKEN_RE = re.compile(r'(202[5-9]|203[01])\s*年|\d{1,2}月\d{1,2}日')
DATE_RE = re.compile(r'(202[0-9])\s*[年\-/]\s*(\d{1,2})\s*[月\-/]\s*(\d{1,2})')

_lock = threading.Lock()
_building = {'ts': 0.0}
_sx_cool = 0.0  # SearXNG 360/sogou 软限流冷却截止（epoch 秒）


def _today():
    return _dt.date.today()


def _norm_key(s):
    return ''.join(str(s or '').split())[:32]


_SRC_SUFFIX_RE = re.compile(r'\s*[-–—|]\s*\S{2,12}$')
_PUNCT_RE = re.compile(r'[！？。，、：;,.!?:~～\s]')


def _news_key(t):
    # 新闻去重键：剥掉 " - 凤凰网"/" - 腾讯新闻" 等来源后缀与标点，
    # 同一情报多源转载（成都11月排期表！-腾讯 vs -凤凰）只留一条。
    t = _SRC_SUFFIX_RE.sub('', str(t or ''))
    return _PUNCT_RE.sub('', t)[:24]



# ── DeepSeek 调用（token-plan 域）────────────────────────────────
def _cfg():
    with open(CONFIG_PATH, encoding='utf-8') as f:
        cfg = json.load(f)
    prov = (cfg.get('models') or {}).get('providers', {}).get('qwenTokenPersonalProvider', {})
    base, key = prov.get('baseUrl', ''), prov.get('apiKey', '')
    if not base or not key or key.startswith('{'):
        raise RuntimeError('token-plan provider 未配置')
    u = urllib.parse.urlparse(base.rstrip('/') + '/chat/completions')
    return u.hostname, u.path, key


def _ds_once(body_dict, host, path, key, timeout):
    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection(host, timeout=timeout, context=ctx)
    try:
        conn.request('POST', path,
                     body=json.dumps(body_dict, ensure_ascii=False).encode('utf-8'),
                     headers={'Content-Type': 'application/json',
                              'Authorization': ('Bearer ' + key)})
        r = conn.getresponse()
        raw = r.read().decode('utf-8', 'replace')
    finally:
        conn.close()
    if r.status != 200:
        raise RuntimeError('DS HTTP %s: %s' % (r.status, re.sub(r'sk-\S+', '***', raw[:180])))
    d = json.loads(raw)
    msg = (d.get('choices') or [{}])[0].get('message') or {}
    return (msg.get('content') or '').strip()


def _ds(prompt, max_tokens=4000, timeout=150):
    """deepseek-v4-pro 单轮 → content（v4-pro 思考吃光 token 的坑见文件头台账）。"""
    host, path, key = _cfg()
    base = {
        'model': DS_MODEL,
        'messages': [
            {'role': 'system', 'content': '你是严格的二次元展会/联名情报抽取器，只输出JSON。'},
            {'role': 'user', 'content': prompt},
        ],
        'temperature': 0.2,
        'max_tokens': max_tokens,
    }
    content = _ds_once(dict(base, reasoning_effort='low'), host, path, key, timeout)
    if not content:
        content = _ds_once(dict(base, enable_thinking=False), host, path, key, timeout)
    if not content:
        raise RuntimeError('DS 空回复(两轮)')
    return content


def _json_from(text):
    t = text.strip()
    if t.startswith('```'):
        t = re.sub(r'^```\w*\n?', '', t)
        t = re.sub(r'\n?```$', '', t).strip()
    for op, cl in (('{', '}'), ('[', ']')):
        i = t.find(op)
        if i < 0:
            continue
        depth = 0
        for j in range(i, len(t)):
            if t[j] == op:
                depth += 1
            elif t[j] == cl:
                depth -= 1
                if depth == 0:
                    return json.loads(t[i:j + 1])
    raise ValueError('无JSON')


# ── 源①：SearXNG 强制 360search+sogou（title+content 摘要，常带日期）──
def _sx(q):
    params = {'q': q, 'format': 'json', 'language': 'zh-CN', 'safesearch': 0,
              'engines': SEARX_ENGINES}
    url = SEARXNG + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 collab-radar/1.0'})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode('utf-8', 'ignore'))
    out = []
    for it in (data.get('results') or [])[:8]:
        title = (it.get('title') or '').strip()
        content = (it.get('content') or '').strip()
        url2 = it.get('url') or ''
        if not title or not url2.startswith('http'):
            continue
        m = DATE_RE.search((it.get('publishedDate') or '') + ' ' + content[:40])
        d = '%s-%02d-%02d' % (m.group(1), int(m.group(2)), int(m.group(3))) if m else ''
        out.append({'title': title[:100], 'url': url2, 'time': d,
                    'snippet': content[:140], 'src': 'SearXNG'})
    return out


# ── 源②：Google News RSS（title 常含定档日期；正文是 JS 壳不抓）────
def _rss_news(q, days=150):
    from email.utils import parsedate_to_datetime
    params = {'q': q, 'hl': 'zh-CN', 'gl': 'CN', 'ceid': 'CN:zh-Hans'}
    url = GNEWS + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        xml = r.read().decode('utf-8', 'ignore')
    out = []
    now = _dt.datetime.now(_dt.timezone.utc)
    for it in re.findall(r'<item>(.*?)</item>', xml, re.S):
        tm = re.search(r'<title>([^<]*)</title>', it)
        lk = re.search(r'<link>([^<]*)</link>', it)
        pd = re.search(r'<pubDate>([^<]*)</pubDate>', it)
        title = (tm.group(1).strip() if tm else '').strip()
        link = (lk.group(1).strip() if lk else '').strip()
        if not title or not link:
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
        out.append({'title': title[:100], 'url': link, 'time': pub,
                    'snippet': '', 'src': 'Google News'})
    return out


def _gather(extra_q=()):
    """双源合池：SearXNG 在前（有正文摘要），GNews 补量；关键词粗筛+去重。"""
    queries = [q for q in (list(EXPO_QUERIES) + list(extra_q)) if q][:14]
    pool, seen = [], set()

    def add(items):
        for x in items:
            if not any(k in x['title'] for k in EXPO_KW):
                continue
            k = _news_key(x['title'])
            if not k or k in seen:
                continue
            seen.add(k)
            pool.append(x)

    # 实测坑（2026-11-25）：360search/sogou 连续多轮批量查询后整点返回 0（软限流：
    # unresponsive_engines 为空，10~40 分钟自恢复）——sx 串行+0.8s 限速；一轮全 0 则
    # 冷却 20 分钟只做 RSS；引擎集加 bing（未限流，相关性差些但过 EXPO_KW 后仍有增量）。
    global _sx_cool
    with ThreadPoolExecutor(max_workers=4) as ex:
        rfuts = {ex.submit(_rss_news, q): q for q in queries}
        if time.time() >= _sx_cool:
            sx_hits = 0
            for q in queries:
                try:
                    rs = _sx(q)
                    sx_hits += len(rs)
                    add(rs)
                except Exception:
                    pass
                time.sleep(0.8)
            if sx_hits == 0:
                _sx_cool = time.time() + 1200
        for f in as_completed(rfuts):
            try:
                add(f.result())
            except Exception:
                continue
    pool.sort(key=lambda x: (bool(LIST_RE.search(x['title'])), x['time'] or ''), reverse=True)
    return pool[:36]


# ── 抽取：标题池一轮 + 排期正文一轮 ─────────────────────────────
_EXPO_PROMPT = """从以下「漫展情报」列表（含新闻标题与搜索摘要）中抽取明确提及的漫展/动漫展/同人展/游戏展活动条目。今天是 {today}。
严格输出JSON：{{"events":[{{"name":"展会全名","city":"城市","start":"YYYY-MM-DD或YYYY-MM","end":"同上或空","venue":"场馆(如有)","idx":条目编号}}]}}
规则：
1. 只收文字里白纸黑字出现的展会与日期；没有日期线索就不收，禁止编造
2. 摘要里的"2023年/2024年"等往年旧事一律忽略；日期以贴近今年/明年的语境为准，往年活动一律丢弃
3. 同一展会只输出一次（选日期最明确的idx）
4. name 保留原文叫法（如 CP31、第29届IJOY漫展）
5. 最多18条，宁缺毋滥
情报列表：
{items}
只输出JSON，不要代码围栏。"""

_ART_PROMPT = """今天是 {today}。从下面这篇漫展汇总文章正文中抽取**有明确日期**的漫展/动漫展/同人展条目（展会名、城市、起止日期、场馆）。
严格输出JSON：{{"events":[{{"name":"...","city":"...","start":"YYYY-MM-DD或YYYY-MM","end":"同上或空","venue":"场馆(如有)"}}]}}
规则：只收正文白纸黑字给了日期的展会；往年（早于今年3个月以上）的日期一律丢弃；禁止编造；最多12条。
标题：{title}
正文节选：
{body}
只输出JSON，不要代码围栏。"""


def _norm_event(e, url, news_date):
    """DS 条目 → 前端 EVS 形态；日期不完整/过旧/离谱丢弃。失败返回 None。"""
    name = str(e.get('name') or '').strip()
    if not name:
        return None
    today = _today()
    s = str(e.get('start') or '').strip()
    if re.match(r'^\d{4}-\d{2}$', s):
        s += '-01'
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return None
    try:
        sd = _dt.date.fromisoformat(s)
    except ValueError:
        return None
    if (today - sd).days > 45 or (sd - today).days > 400:
        return None
    e2 = str(e.get('end') or '').strip()
    if re.match(r'^\d{4}-\d{2}$', e2):
        e2 += '-28'
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', e2) or e2 < s:
        e2 = s
    return {
        't': name[:60], 'city': str(e.get('city') or '').strip()[:20] or '待定',
        'prov': '', 's': s, 'e': e2, 'addr': str(e.get('venue') or '').strip()[:60],
        'img': '', 'price': '', 'url': url, 'cat': '综合展',
        '_live': True, 'news_date': news_date or '',
    }


def _strip_html(html):
    html = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.S | re.I)
    txt = re.sub(r'<[^>]+>', ' ', html)
    txt = re.sub(r'&[a-z#0-9]+;', ' ', txt)
    return re.sub(r'\s+', ' ', txt).strip()


def _fetch_article(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=18) as r:
            return _strip_html(r.read(400 * 1024).decode('utf-8', 'ignore'))[:2800]
    except Exception:
        return ''


def _extract(pool):
    if not pool:
        return []
    out = []
    lines = '\n'.join('%d. [%s] %s || %s' % (i + 1, x['time'] or '?', x['title'], x['snippet'][:110])
                      for i, x in enumerate(pool))
    try:
        raw = _ds(_EXPO_PROMPT.format(today=_today().isoformat(), items=lines))
        d = _json_from(raw)
        for e in (d.get('events') if isinstance(d, dict) else []) or []:
            idx = e.get('idx')
            src = pool[idx - 1] if isinstance(idx, int) and 1 <= idx <= len(pool) else None
            it = _norm_event(e, src['url'] if src else '', src['time'] if src else '')
            if it:
                out.append(it)
    except Exception:
        pass
    # 排期表正文富矿（本地宝类静态站）逐篇抽取
    arts = [x for x in pool
            if LIST_RE.search(x['title'])
            and any(h in (urllib.parse.urlparse(x['url']).hostname or '') for h in ARTICLE_HOSTS)][:3]
    for art in arts:
        body = _fetch_article(art['url'])
        if len(body) < 800 or len(DATE_TOKEN_RE.findall(body)) < 6:
            continue
        try:
            raw = _ds(_ART_PROMPT.format(today=_today().isoformat(),
                                         title=art['title'], body=body))
            d = _json_from(raw)
            for e in (d.get('events') if isinstance(d, dict) else []) or []:
                it = _norm_event(e, art['url'], art.get('time', ''))
                if it:
                    out.append(it)
        except Exception:
            continue
    merged, seen = [], set()
    for x in out:
        k = ''.join(x['t'].split())[:18]
        if k in seen:
            continue
        seen.add(k)
        merged.append(x)
    merged.sort(key=lambda x: x['s'])
    return merged


# ── DeepSeek 动态查询词（联动流 + 漫展流共用）───────────────────
_Q_PROMPT = """今天是 {today}。为「{kind}」资讯聚合搜索生成 8 组中文搜索查询词。
{hint}
要求：每组 2-4 个词空格分隔；优先当季/本月热点与具体品牌或展会名；不要通用套话。
只输出 JSON 字符串数组（如 ["瑞幸 联名 12月", ...]），不要解释、不要围栏。"""

_Q_HINT = {
    'collab': '用途：搜索最近两周中国市场的品牌×IP二次元联名/联动情报（茶饮、快餐、服饰、玩具、谷子、美妆）。',
    'expo': '用途：搜索最近一个月中国漫展/同人展的定档、时间表、开票、场馆公告新闻。',
}


def extra_queries(kind='collab', allow_build=True):
    """DeepSeek 生成的动态搜索词；24h 文件缓存，失败 1h 内不重试。
    allow_build=False 只读缓存——请求路径禁阻塞式 DS 调用（性能红线同 vision）。"""
    try:
        c = json.load(open(QPATH, encoding='utf-8'))
    except Exception:
        c = {}
    ent = c.get(kind) or {}
    now = time.time()
    if ent.get('ts') and now - ent['ts'] < TTL_Q:
        return ent.get('q') or []
    if not allow_build or (ent.get('fail') and now - ent['fail'] < 3600):
        return []
    try:
        raw = _ds(_Q_PROMPT.format(today=_today().isoformat(), kind=kind,
                                   hint=_Q_HINT.get(kind, '')), max_tokens=800, timeout=120)
        arr = _json_from(raw)
        out = [str(x).strip()[:40] for x in arr if x and len(str(x).strip()) >= 3][:8] \
            if isinstance(arr, list) else []
        c[kind] = {'ts': now, 'q': out}
    except Exception:
        c[kind] = dict(c.get(kind) or {}, fail=now)
        out = []
    try:
        tmp = QPATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(c, f, ensure_ascii=False)
        os.replace(tmp, QPATH)
    except Exception:
        pass
    return out


def ensure_queries_async(kind='collab'):
    """后台刷新动态查询词（绝不在请求路径阻塞）；30 分钟防重入。"""
    try:
        c = json.load(open(QPATH, encoding='utf-8'))
    except Exception:
        c = {}
    ent = c.get(kind) or {}
    now = time.time()
    if (ent.get('ts') and now - ent['ts'] < TTL_Q) or \
       (ent.get('fail') and now - ent['fail'] < 3600) or \
       (ent.get('building') and now - ent['building'] < 1800):
        return
    try:
        c[kind] = dict(ent, building=now)
        with open(QPATH, 'w', encoding='utf-8') as f:
            json.dump(c, f, ensure_ascii=False)
    except Exception:
        pass
    threading.Thread(target=extra_queries, kwargs={'kind': kind}, daemon=True).start()


# ── 对外：缓存化曝光（请求路径零取数）───────────────────────────
def _read_cache():
    try:
        with open(CACHE_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _build():
    """同步执行一轮 双源取数→抽取→写缓存。返回 (items, news, err)。"""
    err = None
    items, pool = [], []
    try:
        qs = extra_queries('expo')  # 后台线程内允许同步构建
        pool = _gather(qs)
        items = _extract(pool)
        if not items and pool:
            err = 'DS抽取0条(pool=%d)' % len(pool)
    except Exception as e:
        err = str(e)[:160]
    live_news = [{'title': x['title'], 'url': x['url'], 'time': x['time'],
                  'summary': x['snippet'] or (x['title'][:60]), 'src': x['src'],
                  '_live': True} for x in pool[:12]]
    with _lock:
        prev = _read_cache() or {}
        doc = {'ts': time.time(),
               'gen': time.strftime('%Y-%m-%d %H:%M:%S'),
               'items': items, 'news': live_news,
               'pool': len(pool), 'err': err}
        if not items and prev.get('items'):   # 本轮抽取失败保留旧展会
            doc['items'] = prev['items']
            doc['news'] = prev.get('news') or live_news
            doc['stale_items'] = True
        tmp = CACHE_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False)
        os.replace(tmp, CACHE_PATH)
    return items, live_news, err


def get_expo():
    """HTTP 入口：有缓存给缓存；过期给旧缓存+后台刷新（分钟级取数绝不在请求路径）。"""
    c = _read_cache()
    now = time.time()
    if c and now - c.get('ts', 0) < TTL_EXPO:
        return {'ok': True, 'items': c.get('items', []), 'news': c.get('news', []),
                'generated_at': c.get('gen'), 'stale': False, 'error': c.get('err')}
    if not _building.get('ts') or now - _building['ts'] > 900:
        _building['ts'] = now

        def _w():
            try:
                _build()
            except Exception:
                pass
        threading.Thread(target=_w, daemon=True).start()
    if c:
        return {'ok': True, 'items': c.get('items', []), 'news': c.get('news', []),
                'generated_at': c.get('gen'), 'stale': True,
                'note': '实时情报后台更新中…', 'error': c.get('err')}
    return {'ok': True, 'items': [], 'news': [], 'building': True,
            'note': '实时漫展情报首次加载中（约3分钟），期间展示静态存档'}


if __name__ == '__main__':
    its, nws, e = _build()
    print('events:', len(its), 'news:', len(nws), 'err:', e)
    for x in its[:20]:
        print(' -', x['s'], '~', x['e'], '|', x['city'], '|', x['t'])
