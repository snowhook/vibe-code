# -*- coding: utf-8 -*-
"""雪球爬虫：selenium 浏览器会话内 fetch（过 WAF 的唯一可靠路径）

2026-11-25 全面核查重写，实测台账：
- 旧路已死：xueqiu.com/statuses/hot/listV2.json + X-Requested-With 头 → WAF 405 /
  "Failed to fetch"（XRW 触发 CORS 预检被拒）；query/v1 挂在裸域 xueqiu.com 同源 fetch
  也被 WAF 壳页拦截。requests 带旧 cookie（pkl）同样 405。
- 新通路（全部在 www.xueqiu.com 页面上下文内 fetch，credentials:include，无 XRW 头）：
  * 热帖榜:  https://www.xueqiu.com/statuses/hot/listV3.json?page=N&last_id=X
             （每页约5条；翻页有重叠，按 id 去重，实际能拿到 6~9 条——比旧 listV2 少，
              但这是当前 WAF 政策下的天花板；page 参数必须带且 last_id 用上一页尾 id）
  * 个股讨论: https://www.xueqiu.com/query/v1/symbol/search/status.json?symbol=SH600519
             &count=N&source=all （count 生效，能拿满 20~30 条）
  * stock.xueqiu.com/v5/quote.json 页面内可用（行情补充），requests 直连 400。
- 个股页 /S/xxx 直接导航会被 405（WAF 按 UA/指纹），pushState SPA 跳转也不渲染帖子——
  不要走 DOM 抓取个股页，用 search/status.json。
"""
import json
import re
import threading
import time
from datetime import datetime

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36")


def to_xq_symbol(code: str) -> str:
    """600519 -> SH600519, 000001 -> SZ000001, hk01952 -> HK01952, 920099 -> BJ920099"""
    # 实测（2026-11-25）：search/status.json 的 symbol 参数 A股用 SH600519 风格，
    # 港股必须用 "01952.HK" 后缀风格——"HK01952" 返回空 list。
    code = str(code).strip()
    low = code.lower()
    if low.startswith("hk"):
        return re.sub(r"\D", "", low) + ".HK"
    if low.startswith("bj"):
        return "BJ" + re.sub(r"\D", "", low)
    up = code.upper()
    if up.startswith(("SH", "SZ", "BJ")):
        return up
    if up.endswith(".HK"):
        return up
    if code.startswith(("SH", "SZ", "BJ", "HK")):
        digits = re.sub(r"\D", "", code)
        return (digits + ".HK") if code.startswith("HK") else up
    digits = re.sub(r"\D", "", code)
    if "." in code and code.split(".")[-1].upper() == "HK":
        return digits + ".HK"
    if len(digits) == 6:
        if digits.startswith(("92", "43", "83", "87")):   # 北交所
            return "BJ" + digits
        if digits.startswith(("6", "9", "5")):
            return "SH" + digits
        return "SZ" + digits
    if len(digits) <= 5 and digits:                        # 港股纯数字 01952
        return digits + ".HK"
    return up


class XueqiuClient:
    """常驻浏览器会话，页面内 fetch 调雪球 API（线程加锁，Flask threaded=True）"""

    def __init__(self):
        self._driver = None
        self._lock = threading.Lock()

    def _get_driver(self):
        if self._driver is not None:
            return self._driver
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager

        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1400,900")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument(f"user-agent={UA}")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
        driver.get("https://www.xueqiu.com/")
        time.sleep(8)  # 等 WAF JS 执行 + cookie 落地（实测 7s 偶发不够）
        self._driver = driver
        return driver

    def _drop_driver(self):
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    def _fetch_json(self, url: str):
        """页面内 fetch 拿 JSON；失败返回 None；浏览器会话坏死自动重建一次"""
        for attempt in (0, 1):
            try:
                with self._lock:
                    driver = self._get_driver()
                    driver.set_script_timeout(30)
                    js = (
                        "var cb = arguments[0];"
                        "fetch('%s', {credentials:'include', headers:{'Accept':'application/json'}})"
                        ".then(r => r.text()).then(t => cb(t))"
                        ".catch(e => cb(JSON.stringify({__err: String(e)})));" % url
                    )
                    raw = driver.execute_async_script(js)
                d = json.loads(raw)
                if isinstance(d, dict) and d.get("__err"):
                    return None
                return d
            except Exception:
                if attempt == 0:
                    with self._lock:
                        self._drop_driver()
                    continue
                return None
        return None

    def close(self):
        with self._lock:
            self._drop_driver()

    # ---- 业务接口 ----

    def fetch_hot_posts(self, size: int = 20) -> list[dict]:
        """listV3 热帖榜：多轮翻页 + id 去重（实测每轮约5条、轮间有重叠，去重后 6~9 条）"""
        posts, seen, last_id = [], set(), ""
        for page in range(1, 6):
            data = self._fetch_json(
                f"https://www.xueqiu.com/statuses/hot/listV3.json?page={page}&last_id={last_id}")
            if not data:
                break
            lst = data.get("list") or []
            new = 0
            for item in lst:
                oid = item.get("id")
                if not oid or str(oid) in seen:
                    continue
                seen.add(str(oid))
                new += 1
                text = ((item.get("title") or "") + " " +
                        re.sub(r"<[^>]+>", " ", item.get("text") or item.get("description") or "")).strip()
                text = re.sub(r"\s+", " ", text)
                if not text:
                    continue
                uid = item.get("user_id") or (item.get("user") or {}).get("id") or ""
                posts.append({
                    "id": oid,
                    "user": (item.get("user") or {}).get("screen_name", ""),
                    "text": text[:500],
                    "created_at": _fmt_time(item.get("created_at")),
                    "retweet_count": item.get("retweet_count", 0),
                    "fav_count": item.get("fav_count", 0),
                    "reply_count": item.get("reply_count", 0),
                    "symbol": item.get("symbol_id") or item.get("topic_symbol") or "",
                    "source": "雪球",
                    "url": f"https://xueqiu.com/{uid}/{oid}" if uid and oid else "",
                })
            if lst:
                last_id = str(lst[-1].get("id") or "")
            if len(posts) >= size or not new or not data.get("has_next_page"):
                break
            time.sleep(0.8)
        return posts[:size]

    def fetch_stock_posts(self, code: str, count: int = 30) -> list[dict]:
        """个股讨论：www 域 search/status.json（实测 count 生效、可满量返回）"""
        symbol = to_xq_symbol(code)
        data = self._fetch_json(
            f"https://www.xueqiu.com/query/v1/symbol/search/status.json"
            f"?symbol={symbol}&count={count}&source=all")
        if not data:
            return []
        posts = []
        for item in data.get("list", []):
            if not isinstance(item, dict):
                continue
            text = ((item.get("title") or "") + " " +
                    re.sub(r"<[^>]+>", " ", item.get("text") or item.get("description") or "")).strip()
            text = re.sub(r"\s+", " ", text)
            if not text:
                continue
            user = item.get("user") or {}
            uid = user.get("id") or item.get("user_id") or ""
            oid = item.get("id") or ""
            posts.append({
                "id": oid,
                "user": user.get("screen_name", ""),
                "text": text[:500],
                "created_at": _fmt_time(item.get("created_at")),
                "retweet_count": item.get("retweet_count", 0),
                "fav_count": item.get("fav_count", 0),
                "reply_count": item.get("reply_count", 0),
                "symbol": symbol,
                "source": "雪球",
                "url": f"https://xueqiu.com/{uid}/{oid}" if uid and oid else "",
            })
        return posts[:count]


def _fmt_time(ts) -> str:
    if not ts:
        return ""
    try:
        if ts > 1e12:
            ts = ts / 1000
        return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
    except Exception:
        return str(ts)


_client = None
_client_lock = threading.Lock()


def get_client() -> XueqiuClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = XueqiuClient()
        return _client


def fetch_hot_posts(size: int = 20) -> list[dict]:
    return get_client().fetch_hot_posts(size=size)


def fetch_stock_posts(code: str, count: int = 30) -> list[dict]:
    return get_client().fetch_stock_posts(code, count=count)


def close_client():
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None
