# -*- coding: utf-8 -*-
"""统一爬虫入口 + 缓存"""
import json
import os
import time
from datetime import datetime

from crawlers import xueqiu, eastmoney

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "cache")
CACHE_TTL = 300  # 5 分钟


def _cache_path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"{key}.json")


def _cache_get(key: str):
    p = _cache_path(key)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data.get("ts", 0) < CACHE_TTL:
            return data.get("data")
    except Exception:
        pass
    return None


def _cache_set(key: str, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(key), "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "data": data}, f, ensure_ascii=False)


def get_hot_posts(force: bool = False) -> dict:
    """十大热门帖子：雪球 + 东财 分来源"""
    key = "hot_posts"
    if not force:
        cached = _cache_get(key)
        if cached:
            return cached
    xq = xueqiu.fetch_hot_posts(size=20)
    em = eastmoney.fetch_hot_posts(limit=40)
    result = {"xueqiu": xq[:10], "eastmoney": em[:10], "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    _cache_set(key, result)
    return result


def get_hot_stocks(force: bool = False) -> dict:
    """十大热股（东财人气榜）"""
    key = "hot_stocks"
    if not force:
        cached = _cache_get(key)
        if cached:
            return cached
    stocks = eastmoney.fetch_hot_stocks(limit=10)
    result = {"stocks": stocks, "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    _cache_set(key, result)
    return result


def get_stock_posts(code: str, force: bool = False) -> dict:
    """某只股票：雪球 + 东财 帖子"""
    key = f"stock_{code}"
    if not force:
        cached = _cache_get(key)
        if cached:
            return cached
    xq = xueqiu.fetch_stock_posts(code, count=30)
    em = eastmoney.fetch_stock_posts(code, limit=40)
    result = {"code": code, "xueqiu": xq, "eastmoney": em,
              "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    _cache_set(key, result)
    return result


def clear_cache():
    os.makedirs(CACHE_DIR, exist_ok=True)
    for f in os.listdir(CACHE_DIR):
        if f.endswith(".json"):
            try:
                os.remove(os.path.join(CACHE_DIR, f))
            except OSError:
                pass
