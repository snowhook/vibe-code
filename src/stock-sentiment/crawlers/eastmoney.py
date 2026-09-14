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


def fetch_stock_posts(code: str, page: int = 1, limit: int = 40) -> list[dict]:
    """东财股吧某股票帖子（code: 600519 或 600519.SH）"""
    code6 = re.sub(r"\D", "", code)[:6]
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
        m = re.search(r"/news,(\d+),(\d+)\.html", href)
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
            m = re.search(r"/news,(\d+),(\d+)\.html", href)
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


def fetch_hot_stocks(limit: int = 10) -> list[dict]:
    """东财个股人气榜 top N（selenium 渲染）"""
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
        time.sleep(7)
        html = driver.page_source
    finally:
        driver.quit()

    soup = BeautifulSoup(html, "lxml")
    stocks = []
    table = soup.select_one("table.rank_table") or soup.find("table")
    if table is None:
        return []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 9:
            continue
        code_txt = tds[3].get_text(strip=True) if len(tds) > 3 else ""
        if not re.match(r"^\d{6}$", code_txt):
            continue
        rank_txt = tds[1].get_text(strip=True) if len(tds) > 1 else ""
        name_el = tds[4].select_one(f"a.stock_name_{code_txt}") if len(tds) > 4 else None
        name_txt = name_el.get_text(strip=True) if name_el else (tds[4].get_text(strip=True) if len(tds) > 4 else "")
        price_txt = tds[6].get_text(strip=True) if len(tds) > 6 else ""
        change_txt = tds[7].get_text(strip=True) if len(tds) > 7 else ""
        pct_txt = tds[8].get_text(strip=True) if len(tds) > 8 else ""
        fans_txt = tds[9].get_text(strip=True).replace("%", "") if len(tds) > 9 else ""
        fans_parts = fans_txt.split()
        # 热门话题信息（hastopic 行才有）
        topic_name = topic_url = topic_discuss = topic_views = ""
        nametd = tds[4] if len(tds) > 4 else None
        if nametd:
            topic_a = nametd.select_one("a.topic-pc-item[href*='topic']")
            if topic_a:
                topic_name = topic_a.get_text(strip=True).strip("#")
                topic_url = topic_a.get("href", "")
                dissc = nametd.select_one("span.dissc .disnum")
                views = nametd.select_one("span.views .disnum")
                topic_discuss = dissc.get_text(strip=True) if dissc else ""
                topic_views = views.get_text(strip=True) if views else ""
        try:
            rank = int(rank_txt) if rank_txt.lstrip("-").isdigit() else 0
        except ValueError:
            rank = 0
        stocks.append({
            "rank": rank,
            "code": code_txt,
            "name": name_txt,
            "price": _to_float(price_txt),
            "change": _to_float(change_txt),
            "change_pct": _to_float(pct_txt),
            "bull_ratio": _to_float(fans_parts[0]) if len(fans_parts) > 0 else 0,
            "bear_ratio": _to_float(fans_parts[1]) if len(fans_parts) > 1 else 0,
            "topic_name": topic_name,
            "topic_url": ("https:" + topic_url) if topic_url else "",
            "topic_discuss": _to_int(topic_discuss),
            "topic_views": _to_int(topic_views),
            "url": f"{BASE}/list,{code_txt}.html",
        })
    stocks.sort(key=lambda s: s["rank"] if s["rank"] > 0 else 999)
    return stocks[:limit]


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
