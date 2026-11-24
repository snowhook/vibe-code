#!/usr/bin/env python3
"""公众号热门文章数据获取模块 — 搜狗微信搜索直抓（不依赖第三方 API）

能力（接口签名与旧版兼容，server.py 路由无需改动）：
- search(keyword, days, limit, offset)     关键词搜索，翻页，按发布时间排序
- fetch_trending(days, limit, offset)      热门领域关键词轮换聚合"热榜池"，按发布时间排序
- author_rank(days)                        池内按作者聚合热度（发文篇数）

与旧 redfox 方案的差异（微信生态限制，匿名抓取无法获得阅读数）：
- 无阅读数 → 全部按发布时间排序；热榜改为"热门领域词池 + 时间序"
- 防封禁：全局 0.4s 请求间隔 + 10 分钟结果缓存 + 被限流时回退 1 小时内旧缓存
"""

import html as _html
import random
import re
import threading
import time
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HOME = "https://weixin.sogou.com/"
SEARCH_URL = HOME + "weixin?type=2&query=%s&ie=utf8&page=%d"
CACHE_TTL = 600        # 正常结果缓存 10 分钟
STALE_MAX_AGE = 3600   # 限流/故障时允许回退 1 小时内的旧缓存
MIN_INTERVAL = 0.4     # 全局最小请求间隔（秒）
PAGE_SIZE = 10

# 热榜池轮换关键词（覆盖常见热门领域）
HOT_KEYWORDS = ["AI", "科技", "财经", "职场", "创业", "健康养生", "教育",
                "电商", "房产", "新能源", "短视频", "理财"]

# 今日热榜·微信24h热文榜（含真实阅读数，直抓页面表格）
TOPHUB_URL = "https://tophub.today/n/WnBe01o371"
_TOPHUB_TR = re.compile(r'<td><a href="(https?://[^"]+)"[^>]*>([^<]+)</a></td>'
                        r'\s*<td class="ws">([^<]+)</td>')


def _heat_to_num(h):
    h = h.strip().replace('+', '')
    try:
        if '亿' in h:
            return int(float(h.replace('亿', '')) * 1e8)
        if '万' in h:
            return int(float(h.replace('万', '')) * 1e4)
        return int(float(h))
    except Exception:
        return 0


def _fetch_tophub():
    req = urllib.request.Request(TOPHUB_URL, headers={"User-Agent": UA})
    resp = _get_opener().open(req, timeout=20)
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        import gzip as _gz
        raw = _gz.decompress(raw)
    htmltext = raw.decode("utf-8", "ignore")
    trs = re.findall(r"<tr>(.*?)</tr>", htmltext, re.S)
    items = []
    for tr in trs:
        m = _TOPHUB_TR.search(tr)
        if not m:
            continue
        url, title, heat = _html.unescape(m.group(1)), _clean(m.group(2)), m.group(3).strip()
        if not title:
            continue
        items.append({
            "title": title, "author": "", "summary": "",
            "url": url, "ts": 0,
            "date": time.strftime("%Y-%m-%d"),
            "reads": _heat_to_num(heat), "readsText": heat,
            "sogou": url,
        })
    if len(items) < 5:
        raise BlockedError("今日热榜页面结构变化或为空")
    items.sort(key=lambda x: x["reads"], reverse=True)
    return items


_lock = threading.Lock()
_opener = None
_cache = {}            # key -> (ts, data)
_last_req = [0.0]


def _get_opener():
    global _opener
    if _opener is None:
        _opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()))
        _opener.addheaders = [("User-Agent", UA)]
    return _opener


def _fetch(url, referer=HOME, timeout=20):
    with _lock:
        wait = MIN_INTERVAL - (time.time() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        _last_req[0] = time.time()
    req = urllib.request.Request(url)
    req.add_header("Referer", referer)
    return _get_opener().open(req, timeout=timeout).read().decode("utf-8", "ignore")


_BLOCKED = re.compile(r"antispider|验证码|环境异常", re.I)
_ITEM = re.compile(r'<h3>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</h3>(.*?)(?=<h3>|$)', re.S)
_ACC = re.compile(r'all-time-y2">([^<]+)<')
_TS = re.compile(r"timeConvert\('?(\d{9,11})")
_SUM = re.compile(r'class="txt-info"[^>]*>(.*?)</p>', re.S)
_FRAG = re.compile(r"url \+= '(.*?)';")


def _clean(s):
    return _html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _date_range(days):
    now = time.time()
    return (time.strftime("%Y-%m-%d", time.localtime(now - days * 86400))
            + " ~ " + time.strftime("%Y-%m-%d", time.localtime(now)))


class BlockedError(RuntimeError):
    pass


def _search_page(keyword, page):
    """抓一页搜狗结果（约10条）；失败/被限流抛异常"""
    url = SEARCH_URL % (urllib.parse.quote(keyword), page)
    try:
        htmltext = _fetch(url)
    except Exception as e:
        raise BlockedError("搜狗请求失败: %s" % e)
    if "txt-box" not in htmltext:
        if _BLOCKED.search(htmltext):
            raise BlockedError("触发搜狗防抓取限流，几分钟后自动重试")
        if "没有找到" in htmltext or "未找到" in htmltext:
            return []
        raise BlockedError("搜狗页面结构变化或被限流")
    items = []
    for href, title, tail in _ITEM.findall(htmltext):
        acc, ts, summ = _ACC.search(tail), _TS.search(tail), _SUM.search(tail)
        tsec = int(ts.group(1)) if ts else 0
        items.append({
            "title": _clean(title),
            "author": _clean(acc.group(1)) if acc else "",
            "summary": (_clean(summ.group(1))[:120] if summ else ""),
            "ts": tsec,
            "date": time.strftime("%Y-%m-%d", time.localtime(tsec)) if tsec else "",
            # href 可能混入换行/制表等控制字符(HTML内联换行), 会让 urllib 抛 InvalidURL, 必须清洗
            "sogou": "https://weixin.sogou.com" + re.sub(r"[\x00-\x20\x7f]", "", href.replace("&amp;", "&")),
        })
    return items


def _cached(key, fetcher):
    """返回 (data, fresh)。缓存命中直接返回；抓取失败回退≤1h旧缓存；都没有则抛"""
    ent = _cache.get(key)
    if ent and time.time() - ent[0] <= CACHE_TTL:
        return ent[1], True
    err = None
    try:
        data = fetcher()
        _cache[key] = (time.time(), data)
        return data, True
    except Exception as e:
        err = e
    if ent and time.time() - ent[0] <= STALE_MAX_AGE:
        return ent[1], False
    raise BlockedError(str(err) if err else "结果为空")


def _dedupe(items):
    seen, out = set(), []
    for x in items:
        k = x["title"].lower().replace(" ", "")
        if not x["title"] or k in seen:
            continue
        seen.add(k)
        out.append(x)
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out


def _resolve_all(items):
    """搜狗 /link → mp.weixin.qq.com 直链；失败则保留 /link（浏览器打开会自动跳转）"""
    for it in items:
        it["url"] = it["sogou"]
        try:
            jump = _fetch(it["sogou"])
            mp = "".join(_FRAG.findall(jump)).replace("@", "")
            if mp.startswith("https://mp.weixin.qq.com/"):
                it["url"] = mp
        except Exception:
            pass


def _pub(x, resolve_fail_ok=True):
    return {"title": x["title"], "author": x["author"],
            "url": x.get("url") or x["sogou"],
            "date": x["date"], "summary": x["summary"]}


def search(keyword, days=7, limit=10, offset=0):
    days = max(1, min(int(days or 7), 30))
    limit = max(1, min(int(limit or 10), 50))
    offset = max(int(offset or 0), 0)
    if not keyword:
        return {"ok": False, "error": "关键词不能为空"}
    raw, fresh_all = [], True
    pages = range(offset // PAGE_SIZE + 1, (offset + limit - 1) // PAGE_SIZE + 2)
    for p in pages:
        try:
            data, fresh = _cached(("search", keyword, p),
                                  lambda p=p: _search_page(keyword, p))
        except BlockedError as e:
            if not raw:
                return {"ok": False, "error": str(e)}
            break
        raw.extend(data)
        fresh_all = fresh_all and fresh
    cutoff = time.time() - days * 86400
    items = _dedupe([x for x in raw if x.get("ts", 0) >= cutoff])
    page_items = items[offset:offset + limit]
    _resolve_all(page_items)
    r = {"ok": True, "keyword": keyword, "total": len(items),
         "articles": [_pub(x) for x in page_items],
         "dateRange": _date_range(days), "offset": offset, "limit": limit,
         "sort": "time", "source": "搜狗微信直抓 · 按发布时间排序"}
    if not fresh_all:
        r["note"] = "数据为近1小时缓存快照（搜狗暂时限流）"
    return r


def fetch_trending(days=2, limit=10, offset=0):
    days = max(1, min(int(days or 2), 30))
    limit = max(1, min(int(limit or 10), 50))
    offset = max(int(offset or 0), 0)
    # 首选：今日热榜·微信24h热文榜（真实阅读数）
    try:
        data, fresh = _cached(("tophub",), _fetch_tophub)
        page = data[offset:offset + limit]
        r = {"ok": True, "total": len(data), "articles": page,
             "dateRange": "近24小时", "offset": offset, "limit": limit,
             "sort": "reads", "source": "今日热榜·微信24h热文榜（直抓）"}
        if not fresh:
            r["note"] = "数据为近1小时缓存快照（热榜源暂时限流）"
        return r
    except BlockedError:
        pass  # 兜底：词池聚合（按时间、无阅读数）
    bucket = int(time.time() // CACHE_TTL)
    kws = random.Random(bucket).sample(HOT_KEYWORDS, 3)
    raw, fresh_all = [], True
    for k in kws:
        try:
            data, fresh = _cached(("search", k, 1), lambda k=k: _search_page(k, 1))
        except BlockedError:
            continue
        raw.extend(data)
        fresh_all = fresh_all and fresh
    if not raw:
        return {"ok": False, "error": "搜狗暂时不可用/被限流，请稍后重试"}
    cutoff = time.time() - days * 86400
    items = _dedupe([x for x in raw if x.get("ts", 0) >= cutoff])
    if not items:
        return {"ok": False, "error": "所选时间窗内暂无文章，可尝试更长时间范围"}
    page_items = items[offset:offset + limit]
    _resolve_all(page_items)
    r = {"ok": True, "total": len(items),
         "articles": [_pub(x) for x in page_items],
         "dateRange": _date_range(days), "offset": offset, "limit": limit,
         "sort": "time", "poolKeywords": kws,
         "source": "词池聚合兜底 · 按发布时间排序（热榜源暂不可用）"}
    if not fresh_all:
        r["note"] = "数据为近1小时缓存快照（搜狗暂时限流）"
    return r


def author_rank(days=7, limit=20):
    """池内作者聚合排名（按发文篇数）。不解析直链（省请求，/link 浏览器自动跳转）"""
    days = max(1, min(int(days or 7), 30))
    limit = max(1, min(int(limit or 20), 50))
    bucket = int(time.time() // CACHE_TTL)
    kws = random.Random(bucket + 7919).sample(HOT_KEYWORDS, 6)
    raw, fresh_all = [], True
    for k in kws:
        for p in (1, 2):
            try:
                data, fresh = _cached(("search", k, p),
                                      lambda k=k, p=p: _search_page(k, p))
            except BlockedError:
                continue
            raw.extend(data)
            fresh_all = fresh_all and fresh
    cutoff = time.time() - days * 86400
    items = _dedupe([x for x in raw if x.get("ts", 0) >= cutoff])
    if not items:
        return {"ok": False, "error": "搜狗暂时不可用/被限流或无数据，请稍后重试"}
    amap = {}
    for a in items:
        name = (a.get("author") or "").strip()
        if not name:
            continue
        slot = amap.setdefault(name, {"name": name, "count": 0, "articles": []})
        slot["count"] += 1
        slot["articles"].append(a)
    ranked = sorted(amap.values(),
                    key=lambda x: (x["count"], x["articles"][0]["ts"] if x["articles"] else 0),
                    reverse=True)[:limit]
    out = []
    for r in ranked:
        out.append({
            "name": r["name"], "count": r["count"],
            "articles": [{"title": x["title"], "url": x["sogou"], "date": x["date"]}
                         for x in r["articles"][:8]],
        })
    res = {"ok": True, "days": days, "dateRange": _date_range(days),
           "totalArticles": len(items), "totalAuthors": len(amap), "ranking": out,
           "metric": "发文篇数", "source": "搜狗微信直抓（热门领域词池聚合）"}
    if not fresh_all:
        res["note"] = "数据为近1小时缓存快照（搜狗暂时限流）"
    return res
