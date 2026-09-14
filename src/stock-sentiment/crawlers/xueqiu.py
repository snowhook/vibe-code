# -*- coding: utf-8 -*-
"""雪球爬虫：全程 selenium 浏览器会话内 fetch（过 WAF 的唯一可靠路径）

原理：阿里云 WAF 校验 TLS 指纹 + localStorage token，纯 requests/curl_cffi 均被拦。
方案：无头 Chrome 访问首页执行 WAF JS 种 token，之后在同一会话内用 fetch 调 API。
"""
import json
import re
import time
from datetime import datetime

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")


def to_xq_symbol(code: str) -> str:
    """600519 -> SH600519, 000001 -> SZ000001, 300750 -> SZ300750"""
    code = code.strip()
    if code.startswith(("SH", "SZ", "BJ")):
        return code.upper()
    if len(code) == 6 and code.isdigit():
        if code.startswith(("6", "9", "5")):
            return "SH" + code
        return "SZ" + code
    return code.upper()


class XueqiuClient:
    """常驻浏览器会话，页面内 fetch 调雪球 API"""

    def __init__(self):
        self._driver = None

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
        driver.get("https://xueqiu.com/")
        time.sleep(7)  # 等 WAF JS 执行 + token 落地
        self._driver = driver
        return driver

    def _fetch_json(self, url: str) -> dict | None:
        driver = self._get_driver()
        js = f"""
        fetch('{url}', {{headers: {{'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}}}})
          .then(r => r.text())
          .then(t => document.title = 'XQRESULT:' + t)
          .catch(e => document.title = 'XQERROR:' + e)
        """
        driver.execute_script(js)
        time.sleep(3.5)
        title = driver.title
        if title.startswith("XQRESULT:"):
            try:
                return json.loads(title[len("XQRESULT:"):])
            except json.JSONDecodeError:
                return None
        return None

    def close(self):
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    # ---- 业务接口 ----

    def fetch_hot_posts(self, size: int = 20) -> list[dict]:
        url = f"https://xueqiu.com/statuses/hot/listV2.json?since_id=-1&max_id=-1&size={size}"
        data = self._fetch_json(url)
        if not data:
            return []
        posts = []
        for item in data.get("items", []):
            os_ = item.get("original_status") or {}
            text = (os_.get("title") or "") + " " + (os_.get("description") or os_.get("text") or "")
            text = re.sub(r"<[^>]+>", " ", text).strip()
            if not text:
                continue
            user = (os_.get("user") or {}).get("screen_name", "")
            uid = os_.get("user_id", "")
            oid = os_.get("id", "")
            posts.append({
                "id": oid,
                "user": user,
                "text": text[:500],
                "created_at": _fmt_time(os_.get("created_at")),
                "retweet_count": os_.get("retweet_count", 0),
                "fav_count": os_.get("fav_count", 0),
                "reply_count": os_.get("reply_count", 0),
                "symbol": os_.get("symbol", ""),
                "source": "雪球",
                "url": f"https://xueqiu.com/{uid}/{oid}" if uid and oid else "",
            })
        return posts

    def fetch_stock_posts(self, code: str, count: int = 30) -> list[dict]:
        symbol = to_xq_symbol(code)
        url = f"https://xueqiu.com/query/v1/symbol/search/status.json?symbol={symbol}&count={count}"
        data = self._fetch_json(url)
        if not data:
            return []
        posts = []
        for item in data.get("list", []):
            text = (item.get("text") or item.get("description") or "").strip()
            text = re.sub(r"<[^>]+>", " ", text)
            if not text:
                continue
            user = (item.get("user") or {}).get("screen_name", "")
            uid = item.get("user_id", "")
            oid = item.get("id", "")
            posts.append({
                "id": oid,
                "user": user,
                "text": text[:500],
                "created_at": _fmt_time(item.get("created_at")),
                "retweet_count": item.get("retweet_count", 0),
                "fav_count": item.get("fav_count", 0),
                "reply_count": item.get("reply_count", 0),
                "symbol": symbol,
                "source": "雪球",
                "url": f"https://xueqiu.com/{uid}/{oid}" if uid and oid else "",
            })
        return posts


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


def get_client() -> XueqiuClient:
    global _client
    if _client is None:
        _client = XueqiuClient()
    return _client


def fetch_hot_posts(size: int = 20) -> list[dict]:
    return get_client().fetch_hot_posts(size=size)


def fetch_stock_posts(code: str, count: int = 30) -> list[dict]:
    return get_client().fetch_stock_posts(code, count=count)


def close_client():
    global _client
    if _client is not None:
        _client.close()
        _client = None
