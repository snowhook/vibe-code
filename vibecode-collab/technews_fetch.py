#!/usr/bin/env python3
"""科技新闻日报取数管道（2026-11-25，由 AI 新闻日报升级而来）

背景：旧 ai-news.html 靠浏览器直连 rss2json 代理 + 手工 news-cache.json，
缓存停在 2026-07-29 早已失效。本管道改服务端直连 RSS（服务器无 CORS 问题），
按栏目分类缓存，30 分钟 TTL，请求路径零阻塞（同 expo_fetch 模式）。

栏目：AI前沿 / 硬件·数码 / 产业·商业 / 开源·极客 / 论文·前沿（arXiv）。
源可用性实测（2026-11-25，带 Chrome UA）：量子位/雷锋网/IT之家/爱范儿/钛媒体/
少数派/Solidot/arXiv cs.AI 可取；36氪 feed 返回 0 条（已废）、虎嗅 405、
cnbeta 源质量存疑——勿再加回。
"""
import html as _html
import json
import os
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime

APP = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(APP, '.technews-cache.json')
TTL = 30 * 60
MAX_AGE_DAYS = 7
PER_COL = 15

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

# (key, 显示名, feed url, 主栏目)
SOURCES = [
    ('qbitai', '量子位', 'https://www.qbitai.com/feed', 'ai'),
    ('leiphone', '雷锋网', 'https://www.leiphone.com/feed', 'ai'),
    ('ithome', 'IT之家', 'https://www.ithome.com/rss/', 'hardware'),
    ('ifanr', '爱范儿', 'https://www.ifanr.com/feed', 'hardware'),
    ('tmtpost', '钛媒体', 'https://www.tmtpost.com/rss', 'biz'),
    ('sspai', '少数派', 'https://sspai.com/feed', 'geek'),
    ('solidot', 'Solidot', 'https://www.solidot.org/index.rss', 'geek'),
    ('arxiv', 'arXiv cs.AI', 'https://rss.arxiv.org/rss/cs.AI', 'paper'),
]

COLUMNS = [
    ('ai', 'AI 前沿', '🤖'),
    ('hardware', '硬件·数码', '📱'),
    ('biz', '产业·商业', '💰'),
    ('geek', '开源·极客', '🛠'),
    ('paper', '论文·前沿', '🔬'),
]

# 标题命中即归入 ai（覆盖源主栏目）；paper 源不参与覆盖
AI_KWS = ('AI', 'ai', '人工智能', '大模型', '模型', '智能体', '具身', '机器人',
          '算力', '英伟达', 'OpenAI', 'Anthropic', 'GPT', 'LLM', '机器学习',
          '深度学习', '神经网络', '多模态', 'AGI', '千问', '文心', '豆包', 'DeepSeek')

_lock = threading.Lock()
_building = {'ts': 0.0}

_ITEM_RE = re.compile(r'<item[\s>].*?</item>', re.S | re.I)
_CDATA_RE = re.compile(r'<!\[CDATA\[(.*?)\]\]>', re.S)


def _tag(block, name):
    m = re.search(r'<%s(?:\s[^>]*)?>(.*?)</%s>' % (name, name), block, re.S | re.I)
    if not m:
        return ''
    v = m.group(1)
    v = _CDATA_RE.sub(r'\1', v)
    v = re.sub(r'<[^>]+>', ' ', v)
    v = _html.unescape(v)
    return re.sub(r'\s+', ' ', v).strip()


def _parse_date(s):
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return time.strptime(s[:19], fmt)
        except Exception:
            continue
    return None


def _fetch_source(key, label, url, col):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=18) as r:
        raw = r.read().decode('utf-8', 'ignore')
    out = []
    now = time.time()
    for block in _ITEM_RE.findall(raw)[:80]:
        title = _tag(block, 'title')
        link = _tag(block, 'link') or _tag(block, 'guid')
        if not title or not link:
            continue
        dt = _parse_date(_tag(block, 'pubDate') or _tag(block, 'dc:date'))
        ts = None
        if dt is not None:
            try:
                ts = dt.timestamp()
            except Exception:
                ts = None
        if ts and now - ts > MAX_AGE_DAYS * 86400:
            continue
        desc = _tag(block, 'description')[:220]
        # arXiv 摘要带 "arXiv:xxxx Announce Type: xx" 前缀噪声
        desc = re.sub(r'arXiv:\S+\s*Announce Type:\s*\S+', '', desc).strip()
        c = col
        if c != 'paper' and any(k in title for k in AI_KWS):
            c = 'ai'
        out.append({'title': title[:160], 'url': link, 'source': label,
                    'date': ts or now, 'desc': desc, 'col': c})
    return out


def _norm_title(t):
    return re.sub(r'[\s！？。，、：;,.!?:~～【】()（）\[\]]', '', str(t or ''))[:36]


def _build():
    """同步执行一轮：并发拉源→去重→分栏→写缓存。返回 (columns_payload, errors)。"""
    items, errors = [], []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_fetch_source, k, l, u, c): (k, l)
                for k, l, u, c in SOURCES}
        for f in as_completed(futs):
            key, label = futs[f]
            try:
                items.extend(f.result())
            except Exception as e:
                errors.append('%s: %s' % (label, type(e).__name__))
    seen, uniq = set(), []
    for x in sorted(items, key=lambda v: -v['date']):
        k = _norm_title(x['title'])
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(x)
    cols = []
    for ckey, clabel, cicon in COLUMNS:
        sub = [x for x in uniq if x['col'] == ckey][:PER_COL]
        cols.append({'key': ckey, 'label': clabel, 'icon': cicon, 'items': sub})
    doc = {'ts': time.time(),
           'gen': time.strftime('%Y-%m-%d %H:%M:%S'),
           'columns': cols, 'total': sum(len(c['items']) for c in cols),
           'errors': errors}
    tmp = CACHE_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False)
    os.replace(tmp, CACHE_PATH)
    return cols, errors


def _read_cache():
    try:
        with open(CACHE_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _ensure_async():
    with _lock:
        if time.time() - _building['ts'] < 600:
            return
        _building['ts'] = time.time()
    threading.Thread(target=_safe_build, daemon=True).start()


def _safe_build():
    try:
        _build()
    except Exception:
        pass


def get_technews():
    """HTTP 入口：有缓存给缓存（过期先给旧的、后台刷新）；无缓存触发构建。"""
    c = _read_cache()
    now = time.time()
    if c and c.get('columns'):
        stale = now - c.get('ts', 0) > TTL
        if stale:
            _ensure_async()
        return {'ok': True, 'columns': c['columns'], 'total': c.get('total', 0),
                'generated_at': c.get('gen'), 'stale': stale,
                'errors': c.get('errors') or [],
                'note': '后台更新中…' if stale else ''}
    _ensure_async()
    return {'ok': True, 'columns': [], 'total': 0, 'building': True,
            'note': '科技新闻首次加载中（约 30 秒），请稍候'}


if __name__ == '__main__':
    cols, errs = _build()
    print('errors:', errs)
    for c in cols:
        print('%s %s: %d' % (c['icon'], c['label'], len(c['items'])))
        for x in c['items'][:3]:
            print('   -', time.strftime('%m-%d %H:%M', time.localtime(x['date'])),
                  x['source'], '|', x['title'][:44])
