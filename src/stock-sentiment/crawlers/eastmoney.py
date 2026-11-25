# -*- coding: utf-8 -*-
"""东方财富股吧爬虫：requests + bs4 抓帖子列表；selenium 抓人气榜热股"""
import re
import time

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")

BASE = "https://guba.eastmoney.com"


def _get_soup(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        if r.status_code != 200:
            return None
        return BeautifulSoup(r.text, "lxml")
    except Exception:
        return None


def _guba_code(code: str) -> str:
    """东财股吧板块代码：A股 600519 -> '600519'；北交所 920099 -> '920099'（实测裸数字即可）；
    港股 hk01952 -> 'hk01952'（实测必须带 hk 前缀，剥掉前缀的 list,01952.html 返回 0 帖——
    旧版 re.sub 剥前缀正是港股全空的 bug 根源）。"""
    s = str(code).strip().lower()
    if s.startswith("hk"):
        return "hk" + re.sub(r"\D", "", s)
    if s.startswith("bj"):
        return re.sub(r"\D", "", s)[:6]
    if s.startswith(("sh", "sz")):
        return re.sub(r"\D", "", s)[:6]
    return re.sub(r"\D", "", s)[:6]


def fetch_stock_posts(code: str, page: int = 1, limit: int = 40) -> list[dict]:
    """东财股吧某股票帖子（code: 600519 / hk01952 / SH600519 均可）"""
    code6 = _guba_code(code)
    url = f"{BASE}/list,{code6}.html" if page == 1 else f"{BASE}/list,{code6},{page}.html"
    soup = _get_soup(url)
    if soup is None:
        return []
    posts = []
    for tr in soup.select("tr.listitem"):
        title_a = tr.select_one("div.title a[href*='/news,']")
        if not title_a:
            continue
        read_el = tr.select_one("div.read")
        reply_el = tr.select_one("div.reply")
        author_el = tr.select_one("div.author a")
        time_el = tr.select_one("div.update")
        href = title_a.get("href", "")
        m = re.search(r"/news,([a-zA-Z]*\d+),(\d+)\.html", href)
        if not m:
            continue
        post_id = m.group(2)
        posts.append({
            "id": post_id,
            "title": title_a.get_text(strip=True),
            "read_count": _parse_num(read_el),
            "reply_count": _parse_num(reply_el),
            "author": author_el.get_text(strip=True) if author_el else "",
            "time": time_el.get_text(strip=True) if time_el else "",
            "source": "东财股吧",
            "url": f"{BASE}/news,{code6},{post_id}.html",
        })
        if len(posts) >= limit:
            break
    return posts


def fetch_hot_posts(page: int = 1, limit: int = 40) -> list[dict]:
    """东财股吧全市场热帖：从人气榜前 N 只股票股吧聚合帖子，按阅读量排序"""
    top_stocks = fetch_hot_stocks(limit=8)
    all_posts = []
    for st in top_stocks:
        code6 = st["code"]
        url = f"{BASE}/list,{code6}.html"
        soup = _get_soup(url)
        if soup is None:
            continue
        for tr in soup.select("tr.listitem"):
            title_a = tr.select_one("div.title a[href*='/news,']")
            if not title_a:
                continue
            href = title_a.get("href", "")
            m = re.search(r"/news,([a-zA-Z]*\d+),(\d+)\.html", href)
            if not m:
                continue
            post_id = m.group(2)
            read_el = tr.select_one("div.read")
            reply_el = tr.select_one("div.reply")
            author_el = tr.select_one("div.author a")
            time_el = tr.select_one("div.update")
            all_posts.append({
                "id": post_id,
                "title": title_a.get_text(strip=True),
                "read_count": _parse_num(read_el),
                "reply_count": _parse_num(reply_el),
                "author": author_el.get_text(strip=True) if author_el else "",
                "time": time_el.get_text(strip=True) if time_el else "",
                "source": "东财股吧",
                "code": code6,
                "stock_name": st.get("name", ""),
                "url": f"{BASE}/news,{code6},{post_id}.html",
            })
        time.sleep(0.5)
    all_posts.sort(key=lambda p: p["read_count"], reverse=True)
    return all_posts[:limit]


def _tencent_quotes(codes: list[str]) -> dict[str, dict]:
    """腾讯行情批量接口补名称/价格。东财人气榜页面的股票名与行情是 JS 异步渲染，
    DOM 里是空的（实测 a.stock_name_xxx 文本为空、价格列 "--"）；
    东财 push2 接口对本服务器 IP 直接拒连，qt.gtimg.cn 实测可用（gbk 编码）。"""
    if not codes:
        return {}
    ids = ",".join(("sh" if c.startswith(("6", "9", "5")) else "sz") + c for c in codes)
    try:
        r = requests.get(f"http://qt.gtimg.cn/q={ids}", timeout=10)
        r.encoding = "gbk"
    except Exception:
        return {}
    out = {}
    for line in r.text.split(";"):
        if "=" not in line:
            continue
        f = line.split("=", 1)[1].strip('"').split("~")
        if len(f) > 32 and f[2]:
            out[f[2]] = {"name": f[1], "price": _to_float(f[3]),
                         "change": _to_float(f[31]), "change_pct": _to_float(f[32])}
    return out


def fetch_hot_stocks(limit: int = 10) -> list[dict]:
    """东财个股人气榜 top N（selenium 渲染 + 腾讯行情补全）

    2026-11-25 核查重写：排名数字不在 td 文本里而是 icon_rankN 类名；
    多空比是 left_percent/right_percent 两个 span（旧代码按空格切 fans 文本永远切不开）；
    名称/价格 DOM 为空，改走 _tencent_quotes 批量补。"""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument(f"user-agent={UA}")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    try:
        driver.get(f"{BASE}/rank")
        time.sleep(8)
        html = driver.page_source
    finally:
        driver.quit()

    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.rank_table") or soup.find("table")
    if table is None:
        return []
    rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 10:
            continue
        code_txt = tds[3].get_text(strip=True)
        if not re.match(r"^\d{6}$", code_txt):
            continue
        m = re.search(r"icon_rank(\d+)", str(tds[0]))
        if m:
            rank = int(m.group(1))
        else:
            txt = tds[0].get_text(strip=True)
            rank = int(txt) if txt.isdigit() else 0
        left_el = tds[9].select_one("span.left_percent")
        right_el = tds[9].select_one("span.right_percent")
        bull = _to_float(left_el.get_text(strip=True)) if left_el else 0.0
        bear = _to_float(right_el.get_text(strip=True)) if right_el else 0.0
        topic_name = topic_url = topic_discuss = ""
        topic_a = tds[4].select_one("a[href*='topic']")
        if topic_a:
            topic_name = topic_a.get_text(strip=True).strip("#")
            href = topic_a.get("href", "")
            topic_url = ("https:" + href) if href.startswith("//") else href
            dis = tds[4].select_one("span.disnum")
            topic_discuss = dis.get_text(strip=True) if dis else ""
        rows.append({
            "rank": rank,
            "code": code_txt,
            "name": "", "price": 0.0, "change": 0.0, "change_pct": 0.0,
            "bull_ratio": bull, "bear_ratio": bear,
            "topic_name": topic_name,
            "topic_url": topic_url,
            "topic_discuss": _to_int(topic_discuss),
            "topic_views": 0,
            "url": f"{BASE}/list,{code_txt}.html",
        })
    quotes = _tencent_quotes([r["code"] for r in rows])
    for st in rows:
        q = quotes.get(st["code"]) or {}
        st["name"] = q.get("name", "")
        st["price"] = q.get("price", 0.0)
        st["change"] = q.get("change", 0.0)
        st["change_pct"] = q.get("change_pct", 0.0)
    rows.sort(key=lambda x: x["rank"] if x["rank"] > 0 else 999)
    return rows[:limit]


def _parse_num(el) -> int:
    if not el:
        return 0
    text = el.get_text(strip=True)
    text = text.replace(",", "")
    mult = 1
    if "万" in text:
        mult = 10000
        text = text.replace("万", "")
    try:
        return int(float(text) * mult) if text else 0
    except ValueError:
        return 0


def _to_float(s: str) -> float:
    s = s.strip().replace("%", "").replace(",", "")
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def _to_int(s) -> int:
    s = str(s or "").strip().replace(",", "")
    try:
        return int(float(s)) if s else 0
    except ValueError:
        return 0
