# -*- coding: utf-8 -*-
"""情绪分析：基于财经词典的情感打分（复用 topnews 方法论并增强）"""

POSITIVE_DICT = {
    "利好": 0.8, "大涨": 0.7, "突破": 0.7, "放量": 0.6, "反弹": 0.5,
    "上涨": 0.4, "新高": 0.6, "增长": 0.4, "超预期": 0.8, "大超预期": 1.0,
    "降息": 0.7, "降准": 0.8, "刺激": 0.5, "扶持": 0.5, "支持": 0.4,
    "获批": 0.5, "签约": 0.3, "合作": 0.3, "盈利": 0.5, "增持": 0.5,
    "回购": 0.5, "流入": 0.5, "宽松": 0.6, "复苏": 0.5, "回暖": 0.4,
    "一字板": 0.9, "地天板": 0.8, "反包": 0.6, "弱转强": 0.6,
    "龙头": 0.5, "放量板": 0.5, "连板": 0.6, "涨停": 0.7,
    "业绩大增": 0.7, "订单暴增": 0.6, "产能扩张": 0.5,
    "低估": 0.7, "价值洼地": 0.7, "买入机会": 0.7, "抄底": 0.6,
    "错杀": 0.6, "上车": 0.5, "主升浪": 0.7, "翻倍": 0.7, "起飞": 0.6,
    "牛股": 0.6, "机会": 0.4, "看好": 0.5, "推荐": 0.4, "加仓": 0.5,
    "底部": 0.5, "筑底": 0.5, "企稳": 0.4, "修复": 0.4, "潜力": 0.4,
    "爆发": 0.6, "黑马": 0.6, "溢价": 0.3, "供不应求": 0.5,
}

NEGATIVE_DICT = {
    "利空": -0.8, "暴跌": -0.9, "跌停": -0.9, "崩盘": -1.0, "恐慌": -0.7,
    "大跌": -0.7, "破位": -0.6, "新低": -0.6, "下滑": -0.4, "不及预期": -0.8,
    "远不及预期": -1.0, "加息": -0.6, "紧缩": -0.6, "制裁": -0.7, "限制": -0.5,
    "处罚": -0.5, "违约": -0.7, "退市": -0.8, "减持": -0.5, "流出": -0.5,
    "风险": -0.5, "战争": -0.7, "冲突": -0.5, "衰退": -0.7, "危机": -0.8,
    "炸板": -0.7, "核按钮": -0.9, "天地板": -0.9, "强转弱": -0.6,
    "闷杀": -0.8, "缩量板": -0.3, "破发": -0.6, "闪崩": -0.9,
    "业绩暴雷": -0.9, "商誉减值": -0.7, "质押爆仓": -0.8,
    "出货": -0.5, "套牢": -0.6, "割肉": -0.6, "跑路": -0.6, "暴雷": -0.8,
    "踩雷": -0.7, "腰斩": -0.8, "阴跌": -0.5, "跳水": -0.7, "核按钮": -0.9,
    "唱空": -0.5, "看空": -0.4, "回避": -0.4, "风险提示": -0.5,
    "减持计划": -0.6, "解禁": -0.4, "亏损": -0.5, "暴亏": -0.7,
}

INTENSIFIERS = {
    "大幅": 1.3, "急剧": 1.4, "猛烈": 1.4, "罕见": 1.3,
    "历史性": 1.5, "史诗级": 1.5, "前所未有": 1.5,
    "微幅": 0.6, "小幅": 0.7, "略微": 0.6,
}

NEGATORS = ["不", "未", "无", "非", "没有", "难以"]

LABEL_MAP = [
    (0.3, 1.0, "乐观"),
    (0.1, 0.3, "偏暖"),
    (-0.1, 0.1, "中性"),
    (-0.3, -0.1, "偏冷"),
    (-1.0, -0.3, "悲观"),
]


def analyze(text: str) -> dict:
    if not text or not text.strip():
        return {"score": 0.0, "label": "中性", "positive_hits": [], "negative_hits": []}

    positive_hits = []
    negative_hits = []

    for kw, weight in POSITIVE_DICT.items():
        if kw in text:
            adjusted = _adjust_weight(kw, weight, text)
            positive_hits.append({"keyword": kw, "weight": round(adjusted, 2)})

    for kw, weight in NEGATIVE_DICT.items():
        if kw in text:
            adjusted = _adjust_weight(kw, weight, text)
            negative_hits.append({"keyword": kw, "weight": round(adjusted, 2)})

    pos_sum = sum(h["weight"] for h in positive_hits)
    neg_sum = sum(abs(h["weight"]) for h in negative_hits)
    total = pos_sum + neg_sum
    if total == 0:
        score = 0.0
    else:
        score = (pos_sum - neg_sum) / total
        score = max(-1.0, min(1.0, score))

    return {
        "score": round(score, 2),
        "label": _score_to_label(score),
        "positive_hits": positive_hits[:5],
        "negative_hits": negative_hits[:5],
    }


def _adjust_weight(keyword: str, base_weight: float, text: str) -> float:
    weight = base_weight
    idx = text.find(keyword)
    if idx > 0:
        prefix = text[max(0, idx - 2):idx]
        for neg in NEGATORS:
            if neg in prefix:
                return -weight * 0.6
        for intensifier, factor in INTENSIFIERS.items():
            if prefix.endswith(intensifier) or intensifier in prefix[-6:]:
                weight *= factor
                break
    return weight


def _score_to_label(score: float) -> str:
    for low, high, label in LABEL_MAP:
        if low <= score < high:
            return label
    if score >= 0.3:
        return "乐观"
    if score <= -0.3:
        return "悲观"
    return "中性"


# ---- 低估/买入信号检测 ----
VALUE_SIGNAL_KEYWORDS = [
    "低估", "买入机会", "严重低估", "价值洼地", "错杀", "抄底", "值得买入",
    "买点", "上车机会", "安全边际", "黄金坑", "底部区域", "跌出机会",
    "便宜", "性价比高", "适合建仓", "可以买了", "买入良机", "最佳买点",
]


def detect_value_signals(posts: list[dict]) -> list[dict]:
    """检测存在低估/买入机会信号的帖子"""
    result = []
    for p in posts:
        text = (p.get("title") or "") + " " + (p.get("text") or "")
        hits = [kw for kw in VALUE_SIGNAL_KEYWORDS if kw in text]
        if hits:
            item = dict(p)
            item["signals"] = hits[:5]
            result.append(item)
    return result


# ---- 帖子情感标注 ----
def annotate_posts(posts: list[dict]) -> list[dict]:
    """给帖子列表打情感标签"""
    out = []
    for p in posts:
        text = (p.get("title") or "") + " " + (p.get("text") or "")
        sent = analyze(text)
        item = dict(p)
        item["sentiment_score"] = sent["score"]
        item["sentiment_label"] = sent["label"]
        out.append(item)
    return out


def aggregate_sentiment(posts: list[dict]) -> dict:
    """聚合一组帖子的情绪统计"""
    if not posts:
        return {"avg_score": 0, "distribution": {"乐观": 0, "偏暖": 0, "中性": 0, "偏冷": 0, "悲观": 0}}
    scores = [p.get("sentiment_score", 0) for p in posts]
    dist = {"乐观": 0, "偏暖": 0, "中性": 0, "偏冷": 0, "悲观": 0}
    for p in posts:
        label = p.get("sentiment_label", "中性")
        dist[label] = dist.get(label, 0) + 1
    return {
        "avg_score": round(sum(scores) / len(scores), 2),
        "distribution": dist,
        "total": len(posts),
    }
