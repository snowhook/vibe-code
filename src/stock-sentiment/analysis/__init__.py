# -*- coding: utf-8 -*-
"""分析聚合入口：关键词、热股统计、情绪、低估信号"""
from collections import Counter
import re

from analysis import sentiment
from analysis.keywords import extract_from_posts

# 热股识别：雪球 $XX(代码) 或 东财帖子 code 字段
CODE_RE = re.compile(r"\$?([A-Z]{2})?(\d{6})")
VALID_PREFIX = ("60", "00", "30", "68", "83", "87", "43", "92", "20")


def get_top_keywords(posts_by_source: dict, top_n: int = 10) -> dict:
    """分来源提取十大热门关键词"""
    result = {}
    for source, posts in posts_by_source.items():
        result[source] = extract_from_posts(posts, top_n=top_n)
    return result


def get_mentioned_hot_stocks(posts_by_source: dict, top_n: int = 10) -> dict:
    """从帖子中统计被高频提及的股票代码（分来源）"""
    result = {}
    for source, posts in posts_by_source.items():
        counter = Counter()
        for p in posts:
            text = (p.get("title") or "") + " " + (p.get("text") or "")
            if p.get("symbol"):
                code = re.sub(r"[A-Z]", "", p["symbol"])
                if code and len(code) == 6:
                    counter[code] += 1
            for m in CODE_RE.finditer(text):
                code = m.group(2)
                if code.startswith(VALID_PREFIX):
                    counter[code] += 1
        result[source] = [{"code": c, "count": n} for c, n in counter.most_common(top_n)]
    return result


def analyze_posts(posts: list[dict]) -> list[dict]:
    """帖子情感标注 + 低估信号检测"""
    annotated = sentiment.annotate_posts(posts)
    return annotated


def build_stock_report(stock_data: dict) -> dict:
    """构建个股舆情报告"""
    report = {"code": stock_data["code"], "fetched_at": stock_data.get("fetched_at", "")}
    sources = {}
    all_posts = []
    for src in ("xueqiu", "eastmoney"):
        posts = stock_data.get(src, [])
        annotated = sentiment.annotate_posts(posts)
        sources[src] = {
            "posts": annotated,
            "sentiment": sentiment.aggregate_sentiment(annotated),
            "keywords": extract_from_posts(annotated, top_n=8),
        }
        all_posts.extend(annotated)
    report["sources"] = sources
    report["total"] = len(all_posts)

    # 综合情绪
    report["sentiment"] = sentiment.aggregate_sentiment(all_posts)

    # 低估/买入信号
    report["value_signals"] = sentiment.detect_value_signals(all_posts)

    # 结论
    avg = report["sentiment"]["avg_score"]
    if avg >= 0.2:
        verdict = "市场情绪偏乐观，舆情整体看好"
        tone = "乐观"
    elif avg <= -0.2:
        verdict = "市场情绪偏悲观，舆情整体看空"
        tone = "悲观"
    else:
        verdict = "市场情绪中性，多空分歧较大"
        tone = "中性"
    report["verdict"] = verdict
    report["tone"] = tone
    if report["value_signals"]:
        report["verdict"] += f"；发现 {len(report['value_signals'])} 条低估/买入信号帖"
    return report
