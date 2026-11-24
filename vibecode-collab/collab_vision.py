#!/usr/bin/env python3
"""collab_vision.py — AI 读图提取联动情报（二次元联动速递增强模块）

背景：很多联动情报以图片形式发布（盘点长图/小红书截图/官宣海报），
纯文本抓取拿不到。本模块用视觉大模型解读文章内图片，拆出结构化联动条目。

设计：
- 后台线程跑分析（VL 调用每张图 5~15s），/api/collab/fetch 立即返回缓存结果，不阻塞前端
- 按图片 URL 缓存（7天），整轮分析间隔 12h
- LLM 配置复用 ~/.openclaw/openclaw.json 的 qwenTokenPersonalProvider（qwen3.6-flash 已验证可读图）
"""
import base64
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

CONFIG_PATH = os.path.expanduser('~/.openclaw/openclaw.json')
SEARXNG = 'http://localhost:8080/search'
PROVIDER = 'qwenTokenPersonalProvider'
VL_MODEL = 'qwen3.6-flash'
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          '.collab-vision-cache.json')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
# 注意: 前缀拆开拼接, 防止消息管道对敏感字样脱敏破坏源码
AUTH_PREFIX = 'Be' + 'arer '

CACHE_TTL_IMG = 7 * 86400     # 单张图片分析结果缓存 7 天
RUN_INTERVAL = 12 * 3600      # 两轮后台分析最小间隔
MAX_ARTICLES = 4              # 每轮最多分析文章数
MAX_IMAGES = 3                # 每篇文章最多分析图片数
VL_TIMEOUT = 90

_lock = threading.Lock()
_state = {'running': False, 'last_run': 0.0, 'last_note': ''}

# 优先分析这些域名（图文质量高、盘点图多）
PREFERRED_DOMAINS = ('cbndata.com', '36kr.com', 'sohu.com', 'sina.com',
                     'qq.com', '163.com', 'ifeng.com', 'thepaper.cn',
                     'jiemian.com', 'huxiu.com', 'donews.com', 'iheima.com')

_IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>', re.I)
_SIZE_RE = re.compile(r'width=["\']?(\d+)', re.I)
_BAD_PATH = re.compile(
    r'logo|icon|avatar|qrcode|erweima|sprite|button|emoji|loading|placeholder|'
    r'\.svg|\.gif|banner-ad|/ad/|wechat-qr|app-download', re.I)

PROMPT = '''你是品牌×IP联名情报分析员。这张图来自文章《{title}》。
仔细读图，提取图中所有"品牌×IP联名/联动"情报（包括盘点表格、长图列表、海报文字、周边实拍）。
输出严格JSON，不要代码围栏：
{{"collabs":[{{"brand":"品牌名","ip":"IP名","product":"联名产品或周边(没有则空字符串)","date_hint":"图中提到的时间,如2026-07或7月19日(没有则空字符串)","summary":"一句话情报,30-60字"}}]}}
规则：
1. 只提取图中真实可见的信息，禁止编造
2. brand=出产品/门店的商业品牌(如麦当劳、瑞幸、优衣库、泡泡玛特)；ip=被联名的动漫/游戏/影视/艺术家IP(如海贼王、chiikawa、原神)。两者绝不可颠倒；两个IP互推(如海绵宝宝×海贼王)时 brand 填发起活动的商业方，实在分不清则 brand 填图中标注的主办方
3. 图中没有联动情报（装饰图/二维码/人物照/纯风景/纯文字评论）则返回 {{"collabs":[]}}
4. 最多12条，按图中出现顺序
5. date_hint 必须照抄图中标注的年份月份（如"2023-10"）；往年旧案例也要如实标注年份，不要省略年份，也不要默认成今年；图中无时间信息则留空字符串
6. summary 里必须点明联名产品/活动形式(新品、周边、主题店、快闪等)
7. 只收录已官宣/已上市的真实联名；网友建议、粉丝自制概念图、恶搞图、"如果有联名"类假想内容一律不收'''


# ── LLM 配置与调用 ──────────────────────────────────────

def _llm_cfg():
    with open(CONFIG_PATH, encoding='utf-8') as f:
        cfg = json.load(f)
    prov = cfg.get('models', {}).get('providers', {}).get(PROVIDER, {})
    key = prov.get('apiKey', '')
    base = prov.get('baseUrl', '')
    if not key or key.startswith('{') or not base:
        raise RuntimeError('LLM provider 未配置 (%s)' % PROVIDER)
    return base.rstrip('/') + '/chat/completions', key


def _post_vl(url, key, image_ref, title):
    body = json.dumps({
        'model': VL_MODEL,
        'messages': [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': image_ref}},
            {'type': 'text', 'text': PROMPT.format(title=title[:60])},
        ]}],
        'max_tokens': 1800,
        'temperature': 0.2,
    }, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST', headers={
        'Content-Type': 'application/json',
        'Authorization': AUTH_PREFIX + key,
    })
    with urllib.request.urlopen(req, timeout=VL_TIMEOUT) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    return data['choices'][0]['message']['content']


def _to_data_url(img_url):
    """下载图片转 base64 data URL（直链被防盗链拦截时的兜底）"""
    req = urllib.request.Request(img_url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read(4 * 1024 * 1024)
        ctype = r.headers.get('Content-Type', 'image/jpeg').split(';')[0]
    if ctype not in ('image/jpeg', 'image/png', 'image/webp', 'image/gif'):
        ctype = 'image/jpeg'
    return 'data:%s;base64,%s' % (ctype, base64.b64encode(raw).decode())


def _extract_json_obj(text):
    t = text.strip()
    if t.startswith('```'):
        t = re.sub(r'^```\w*\n?', '', t)
        t = re.sub(r'\n?```$', '', t).strip()
    start = t.find('{')
    if start < 0:
        raise ValueError('回复无JSON')
    depth = 0
    for i, ch in enumerate(t[start:], start):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i + 1])
    raise ValueError('JSON不完整')


def analyze_image(img_url, article_title):
    """单图分析 → collabs 列表（带缓存）。失败返回 []"""
    cache = _read_cache()
    ent = cache['images'].get(img_url)
    if ent and time.time() - ent['ts'] < CACHE_TTL_IMG:
        return ent['collabs']
    url, key = _llm_cfg()
    collabs = []
    for attempt in (0, 1):  # 0=直链, 1=base64兜底
        try:
            ref = img_url if attempt == 0 else _to_data_url(img_url)
            out = _post_vl(url, key, ref, article_title)
            collabs = _extract_json_obj(out).get('collabs', [])
            if not isinstance(collabs, list):
                collabs = []
            break
        except Exception:
            continue
    cache['images'][img_url] = {'ts': time.time(), 'collabs': collabs}
    _write_cache(cache)
    return collabs


# ── 文章图片提取 ──────────────────────────────────────

def extract_images(article_url, limit=MAX_IMAGES):
    """抓文章页，过滤出正文内容图（去 logo/图标/二维码）"""
    req = urllib.request.Request(article_url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        html = r.read(1500 * 1024).decode('utf-8', 'ignore')
    out = []
    for m in _IMG_RE.finditer(html):
        src = m.group(1).strip()
        if not src or src.startswith('data:'):
            continue
        full = urllib.parse.urljoin(article_url, src)
        if not full.startswith('http'):
            continue
        if _BAD_PATH.search(full):
            continue
        tag = m.group(0)
        wm = _SIZE_RE.search(tag)
        if wm and int(wm.group(1)) < 200:
            continue
        if full not in out:
            out.append(full)
        if len(out) >= limit:
            break
    return out


# ── 条目转换 ──────────────────────────────────────

def _norm_date(s):
    s = (s or '').strip()
    m = re.search(r'(20\d{2})[-/年.](\d{1,2})', s)
    if m:
        return '%s-%02d' % (m.group(1), int(m.group(2)))
    m = re.search(r'(\d{1,2})月(\d{1,2})日?', s)
    if m:
        return '%d-%02d-%02d' % (time.localtime().tm_year, int(m.group(1)), int(m.group(2)))
    return ''


MAX_AGE_DAYS = 120   # 超过此天数的历史联动视为噪音丢弃


def _is_stale(date_str):
    """判断联动时间是否为陈年旧闻（历年盘点图里混着大量往期案例）
    只认带明确年份的日期；无年份/无日期的不误杀"""
    m = re.match(r'(20\d{2})-(\d{1,2})', date_str or '')
    if not m:
        return False
    try:
        y, mo = int(m.group(1)), min(int(m.group(2)), 12)
        then = time.mktime((y, mo, 1, 0, 0, 0, 0, 0, -1))
    except Exception:
        return False
    return (time.time() - then) > MAX_AGE_DAYS * 86400


# 品类推断关键词 → cat（与 collab_fetch/前端 cat 体系一致：tea/food/fash/toy/gu/beauty/game）
CAT_HINTS = [
    ('food', ('肯德基','KFC','麦当劳','必胜客','汉堡王','德克士','塔斯汀','华莱士','海底捞','寿司郎','炸鸡','汉堡')),
    ('fash', ('优衣库','UNIQLO','服装','服饰','T恤','卫衣','球鞋','潮牌','GU','ZARA','HM','H&M')),
    ('toy',  ('泡泡玛特','POPMART','名创优品','miniso','玩具','公仔','手办','盲盒','积木','高达','乐高')),
    ('gu',   ('谷子','吧唧','亚克力','立牌','镭射票','色纸','挂件')),
    ('beauty',('美妆','彩妆','口红','面膜','护肤','完美日记','花西子','小奥汀')),
    ('tea',  ('喜茶','奈雪','古茗','茶百道','霸王茶姬','瑞幸','库迪','星巴克','蜜雪','CoCo','都可','益禾堂','沪上阿姨','茉莉奶白','书亦','一点点','柠季','挪瓦','奶茶','茶饮','咖啡','茶咖')),
]


# 品牌别名归一（去重与品类判定用）
BRAND_ALIASES = {
    'kfc': '肯德基', 'chagee': '霸王茶姬', 'naissnow': '奈雪的茶',
    '奈雪': '奈雪的茶', '瑞幸咖啡': '瑞幸', '星巴克咖啡': '星巴克',
    '麦当劳m记': '麦当劳', 'm记': '麦当劳', 'uniqlo': '优衣库',
    'popmart': '泡泡玛特', 'miniso': '名创优品', 'coco都可': 'CoCo',
}


def _norm_brand(b):
    s = (b or '').strip().lower()
    for k, v in BRAND_ALIASES.items():
        if k in s:
            return v
    return (b or '').strip()


def _infer_cat(article, brand, ip, summary):
    """品类判定优先级：品牌关键词(最强,品牌决定品类) > 图搜查询品类提示 >
    标题/摘要关键词 > 兜底 tea。
    注意：查询 cat 只是"搜了什么"，图里实际品牌才算数（搜谷子可能命中瑞幸周边图）"""
    if brand:
        for cat, kws in CAT_HINTS:
            if any(k in brand for k in kws):
                return cat
    c = article.get('cat')
    if c:
        return c
    blob = ' '.join(str(x) for x in (brand, ip, summary, article.get('title', '')))
    for cat, kws in CAT_HINTS:
        if any(k in blob for k in kws):
            return cat
    return 'tea'


# 假想/恶搞联名过滤：网友建议、概念图等不是真实情报
FAKE_HINTS = ('网友建议', '粉丝自制', '概念图', '假想', '恶搞', '脑洞',
              '如果联名', '建议联名', '自制周边', '非官方', '同人')


def _to_item(c, article):
    brand = (c.get('brand') or '').strip()
    ip = (c.get('ip') or '').strip()
    if not brand and not ip:
        return None
    head = ' × '.join(x for x in (brand, ip) if x)
    title = (head + ' 联名')[:60]
    prod = (c.get('product') or '').strip()
    summ = (c.get('summary') or '').strip()
    # 假想/恶搞联名丢弃（prompt 已约束，这里是代码防线）
    blob_check = summ + ' ' + prod + ' ' + str(article.get('title', ''))
    if any(k in blob_check for k in FAKE_HINTS):
        return None
    parts = [p for p in (summ, ('周边：' + prod) if prod else '') if p]
    snippet = ('📷AI读图｜' + ' · '.join(parts))[:230] if parts else '📷AI读图情报'
    when = _norm_date(c.get('date_hint')) or article.get('time', '')
    if _is_stale(when):
        return None      # 历年盘点图里的往期案例，不是实时情报
    return {'title': title, 'url': article.get('url', ''), 'snippet': snippet,
            'time': when,
            'q': 'vision', 'score': 1, 'src': 'vision',
            '_cat': _infer_cat(article, brand, ip, summ),
            '_brand': brand, '_ip': ip, '_product': prod, '_summary': summ}


# ── 缓存 ──────────────────────────────────────

def _read_cache():
    try:
        with open(CACHE_PATH, encoding='utf-8') as f:
            d = json.load(f)
        d.setdefault('images', {})
        d.setdefault('items', [])
        return d
    except Exception:
        return {'images': {}, 'items': [], 'built_at': 0}


def _write_cache(cache):
    tmp = CACHE_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, CACHE_PATH)


def load_vision_items(limit=30):
    """读取已分析出的联动情报（供 collab_fetch 合并展示）"""
    return _read_cache().get('items', [])[:limit]


def vision_note():
    with _lock:
        note = _state['last_note']
        running = _state['running']
    if running:
        return 'AI读图分析进行中…'
    return note


# ── 后台分析 ──────────────────────────────────────

# 搜狗微信发现源：品牌联名关键词（复用 gzh_trends 的抓取+直链还原）
SOGOU_QUERIES = ['奶茶 联名', '咖啡 联名 动漫', '联名 周边 新品',
                 '肯德基 联名', '麦当劳 联名', '优衣库 联名', '泡泡玛特 联名',
                 '名创优品 联名', '谷子店 联名 周边']

# 图片搜索发现矩阵：(品类, 关键词) —— 覆盖茶饮/快餐/服饰/玩具/谷子/美妆
# 图搜直接给出图片直链(img_src)，无需先抽文章，是自主发现的主力通道
IMG_QUERIES = [
    ('tea',  '奶茶 联名 盘点'),
    ('tea',  '喜茶 联名'),
    ('tea',  '古茗 联名 动漫'),
    ('tea',  '茶百道 联名'),
    ('tea',  '霸王茶姬 联名'),
    ('tea',  '瑞幸 联名 二次元'),
    ('food', '肯德基 联名 周边'),
    ('food', '麦当劳 联名 动漫'),
    ('food', '必胜客 联名'),
    ('food', '汉堡王 联名 动漫'),
    ('fash', '优衣库 UT 联名'),
    ('fash', '服装品牌 联名 动漫'),
    ('fash', '球鞋 联名 动漫'),
    ('toy',  '泡泡玛特 联名'),
    ('toy',  '名创优品 chiikawa 联名'),
    ('toy',  '玩具 联名 二次元 周边'),
    ('gu',   '谷子店 联名 周边'),
    ('gu',   '动漫 谷子 联名 新品'),
    ('beauty', '美妆 联名 动漫'),
    ('all',  '品牌 联名 二次元 合集'),
]

# 图搜噪音域：图库/博物馆/电商纯商品图等无情报价值
NOISE_IMG_HOSTS = (
    'pexels.com', 'unsplash.com', 'artic.edu', 'shutterstock.com', 'istockphoto.com',
    'gettyimages', 'alamy.com', 'pixabay.com', 'wikimedia.org', 'metmuseum.org',
    'pinterest.', 'weibo.com/ajax', 'douyinpic.com', 'alicdn.com', 'taobaocdn.com',
    'sso.sina.com.cn', 'qpic.cn/mmbiz_gif', 'baidu.com/it',
)
MAX_IMG_PER_ROUND = 14      # 每轮最多读图张数（VL 调用每张约 10~20s）
MAX_IMG_PER_CAT = 3         # 单品类每轮上限，保证品类多样性
MAX_VL_CALLS = 16           # 每轮 VL 调用总预算（图搜+文章通道共享）
IMG_TIME_RANGE = 'month'    # 只要近一个月的图，保证"实时"


def _image_search(query, time_range=IMG_TIME_RANGE, limit=10):
    """SearXNG 图片搜索 → [{img, title, page, cat_hint}]，自带噪音过滤"""
    params = {'q': query, 'format': 'json', 'categories': 'images',
              'language': 'zh-CN', 'safesearch': 0}
    if time_range:
        params['time_range'] = time_range
    url = SEARXNG + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        rs = json.loads(r.read().decode('utf-8')).get('results', [])
    out = []
    for it in rs[:limit]:
        img = it.get('img_src') or it.get('thumbnail') or ''
        title = (it.get('title') or '').strip()
        page = it.get('url') or ''
        if not img.startswith('http') or not page.startswith('http'):
            continue
        host = urllib.parse.urlparse(img).netloc
        if any(n in host for n in NOISE_IMG_HOSTS):
            continue
        blob = title + ' ' + (it.get('content') or '')
        if not any(k in blob for k in ('联名', '联动', '跨界', 'コラボ')):
            continue
        if any(k in blob for k in ('下载', '壁纸', '头像', '教程', '矢量', '素材', 'PSD')):
            continue
        out.append({'img': img, 'title': title, 'page': page,
                    'time': (it.get('publishedDate') or '')[:10]})
    return out


def _image_candidates():
    """多品类图搜发现：每品类限额，按图 URL 去重，返回可直接读图的候选"""
    per_cat, seen_img, out = {}, set(), []
    for cat, q in IMG_QUERIES:
        if per_cat.get(cat, 0) >= MAX_IMG_PER_CAT:
            continue
        try:
            hits = _image_search(q)
        except Exception:
            continue
        for h in hits:
            if h['img'] in seen_img:
                continue
            if per_cat.get(cat, 0) >= MAX_IMG_PER_CAT:
                break
            seen_img.add(h['img'])
            per_cat[cat] = per_cat.get(cat, 0) + 1
            out.append({'title': h['title'] or q, 'url': h['page'],
                        'time': h['time'], 'src': 'imgsearch',
                        'imgs': [h['img']], 'cat': cat})
        time.sleep(0.4)   # 温和限速，别把 SearXNG 打挂
    return out


def _sogou_candidates(max_items=6, max_age_days=45):
    """搜狗微信搜近期联名文章，还原 mp.weixin 直链，作为读图候选"""
    try:
        import gzh_trends as gz
    except Exception:
        return []
    cutoff = time.time() - max_age_days * 86400
    out, seen = [], set()
    for q in SOGOU_QUERIES:
        try:
            items = gz._search_page(q, 1)
        except Exception:
            continue
        fresh = [x for x in items if x.get('ts', 0) >= cutoff]
        if not fresh:
            continue
        try:
            gz._resolve_all(fresh[:2])
        except Exception:
            pass
        for it in fresh[:2]:
            u = it.get('url') or it.get('sogou', '')
            if 'mp.weixin' not in u or u in seen:
                continue
            seen.add(u)
            out.append({'title': it['title'], 'url': u,
                        'time': it.get('date', ''), 'src': 'sogou'})
        if len(out) >= max_items:
            break
    return out[:max_items]


def _pick_candidates(live_items):
    """从实时情报里挑值得读图的文章：优先图文站域名 + 品牌命中 + 有日期"""
    def rank(x):
        host = urllib.parse.urlparse(x.get('url', '')).netloc
        dom = 2 if any(d in host for d in PREFERRED_DOMAINS) else 0
        fresh = 1 if x.get('time') else 0
        return (dom, fresh)
    cands = [x for x in live_items if x.get('src') != 'vision' and x.get('url')]
    cands.sort(key=rank, reverse=True)
    # 同域名最多1篇，避免单站霸屏
    out, seen_hosts = [], set()
    for c in cands:
        host = urllib.parse.urlparse(c['url']).netloc
        if host in seen_hosts:
            continue
        seen_hosts.add(host)
        out.append(c)
        if len(out) >= MAX_ARTICLES:
            break
    # 搜狗微信发现源补充（公众号盘点图/海报图质量高）
    try:
        sogou = _sogou_candidates(max_items=3)
        out.extend(sogou)
    except Exception:
        pass
    return out[:MAX_ARTICLES + 3]


def maybe_start_worker(live_items):
    """缓存过期且无 worker 在跑时，启动后台分析（立即返回，不阻塞请求）
    注意：候选收集（图搜/搜狗，耗时可达分钟级）也在线程内跑，
    这里只传入轻量 live 快照，避免拖慢 /api/collab/fetch 响应"""
    with _lock:
        if _state['running']:
            return False
        if time.time() - _state['last_run'] < RUN_INTERVAL:
            return False
        _state['running'] = True
    snap = [{'title': x.get('title', ''), 'url': x.get('url', ''),
             'time': x.get('time', ''), 'src': x.get('src', '')}
            for x in (live_items or [])[:30] if x.get('url')]
    threading.Thread(target=_run, args=(snap,), daemon=True).start()
    return True


def _run(live_snapshot):
    """后台读图主循环：①图搜直发现(多品类) ②文章通道(实时流+搜狗微信)
    已读过的图(7天缓存)直接跳过，VL 调用受 MAX_VL_CALLS 预算约束"""
    n_new = 0
    try:
        cache = _read_cache()
        img_cache = dict(cache.get('images', {}))   # 本地副本，轮内同步
        new_items = []
        vl_calls = [0]                              # 闭包计数

        def process_img(img, art):
            """读一张图 → 新条目列表；预算耗尽返回 None；失败返回 []
            已缓存的图不重调 VL，但仍重转换条目（幂等：上轮中断/旧缓存缺字段也能补齐）"""
            ent = img_cache.get(img)
            if ent and time.time() - ent.get('ts', 0) < CACHE_TTL_IMG:
                out = []
                for c in ent.get('collabs', [])[:12]:
                    it = _to_item(c, art)
                    if it:
                        out.append(it)
                return out
            if vl_calls[0] >= MAX_VL_CALLS:
                return None
            vl_calls[0] += 1
            try:
                collabs = analyze_image(img, art.get('title', ''))
            except Exception:
                return []
            img_cache[img] = {'ts': time.time(), 'collabs': collabs}
            out = []
            for c in collabs[:12]:
                it = _to_item(c, art)
                if it:
                    out.append(it)
            return out

        # ① 图搜直发现：多品类关键词矩阵，主力自主发现通道
        try:
            img_cands = _image_candidates()
        except Exception:
            img_cands = []
        for cand in img_cands:
            if vl_calls[0] >= MAX_VL_CALLS:
                break
            got = process_img(cand['imgs'][0], cand)
            if got:
                new_items.extend(got)
            time.sleep(0.3)

        # ② 文章通道：实时流里的图文站文章 + 搜狗微信公众号文章
        if vl_calls[0] < MAX_VL_CALLS:
            try:
                arts = _pick_candidates(live_snapshot)
            except Exception:
                arts = []
            for art in arts:
                if vl_calls[0] >= MAX_VL_CALLS:
                    break
                try:
                    imgs = art.get('imgs') or extract_images(art['url'])
                except Exception:
                    continue
                for img in imgs[:MAX_IMAGES]:
                    got = process_img(img, art)
                    if got is None:
                        break
                    if got:
                        new_items.extend(got)
                time.sleep(1)

        # 与旧条目按 品牌+IP 归一去重（新的在前）；旧缓存缺 _cat 的就地回填
        seen, merged = set(), []
        for it in new_items + cache.get('items', []):
            it['_brand'] = _norm_brand(it.get('_brand', ''))
            it['_cat'] = _infer_cat(it, it['_brand'],
                                    it.get('_ip', ''), it.get('_summary', ''))
            bi = it['_brand'] + '|' + (it.get('_ip') or '')
            bi = ''.join(bi.split())[:48].lower()
            # 空键(无品牌且无IP)才退回标题去重；非空键一旦重复直接丢弃
            tk = bi if bi.strip('|') else ''.join(it['title'].split())[:40].lower()
            if tk in seen:
                continue
            seen.add(tk)
            merged.append(it)
        cache['items'] = merged[:120]
        cache['built_at'] = time.time()
        _write_cache(cache)
        n_new = len(new_items)
        cats = {}
        for it in new_items:
            cats[it.get('_cat', '?')] = cats.get(it.get('_cat', '?'), 0) + 1
        with _lock:
            _state['last_note'] = 'AI读图新增 %d 条（读图 %d 张，%s）' % (
                n_new, vl_calls[0], time.strftime('%m-%d %H:%M'))
    except Exception as e:
        with _lock:
            _state['last_note'] = 'AI读图失败: %s' % str(e)[:60]
    finally:
        with _lock:
            _state['running'] = False
            _state['last_run'] = time.time()
