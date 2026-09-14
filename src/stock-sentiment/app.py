# -*- coding: utf-8 -*-
"""股票舆情分析网页 - Flask 应用

功能：
1. 十大热门帖子（雪球/东财分来源）
2. 十大热股（东财人气榜）
3. 十大热门关键词（分来源）
4. 输入股票代码 -> 抓取雪球+东财舆情、情绪分析、生成报告
5. 低估/买入机会帖子链接展示

异步加载：首页先渲染骨架，前端 JS 轮询抓取状态显示进度条+计时，完成后填充数据。
"""
import threading
import time

from flask import Flask, render_template, request, jsonify

from crawlers import xueqiu
import crawlers as crawler_api
import analysis as analysis_api
from analysis import sentiment as sentiment_mod

app = Flask(__name__)

# 抓取状态跟踪（供前端进度条/计时）
_load_state = {
    "active": False,
    "stage": "idle",        # idle / xueqiu / eastmoney / stocks / analysis / done
    "stage_label": "",
    "started_at": None,
    "finished_at": None,
}
_state_lock = threading.Lock()

# 抓取阶段定义（顺序 + 进度百分比）
STAGES = [
    ("xueqiu", "正在抓取雪球热帖…", 15),
    ("eastmoney", "正在抓取东财股吧热帖…", 45),
    ("stocks", "正在抓取东财人气榜热股…", 70),
    ("analysis", "正在分析情感与关键词…", 90),
    ("done", "数据加载完成", 100),
]


def _set_stage(stage: str):
    with _state_lock:
        _load_state["stage"] = stage
        for s, label, pct in STAGES:
            if s == stage:
                _load_state["stage_label"] = label
                break


def _load_home_data():
    """加载首页数据（更新阶段状态）"""
    with _state_lock:
        _load_state["active"] = True
        _load_state["started_at"] = time.time()
        _load_state["finished_at"] = None
    try:
        _set_stage("xueqiu")
        hot_posts = crawler_api.get_hot_posts()

        _set_stage("eastmoney")
        hot_stocks = crawler_api.get_hot_stocks()

        _set_stage("analysis")
        posts_by_source = {
            "xueqiu": hot_posts.get("xueqiu", []),
            "eastmoney": hot_posts.get("eastmoney", []),
        }
        for src, posts in posts_by_source.items():
            posts_by_source[src] = sentiment_mod.annotate_posts(posts)

        keywords = analysis_api.get_top_keywords(posts_by_source, top_n=10)
        all_posts = posts_by_source["xueqiu"] + posts_by_source["eastmoney"]
        value_posts = sentiment_mod.detect_value_signals(all_posts)

        return {
            "hot_posts": posts_by_source,
            "hot_stocks": hot_stocks.get("stocks", []),
            "keywords": keywords,
            "value_posts": value_posts,
            "fetched_at": hot_posts.get("fetched_at", ""),
        }
    finally:
        _set_stage("done")
        with _state_lock:
            _load_state["active"] = False
            _load_state["finished_at"] = time.time()


@app.route("/")
def index():
    # 始终渲染骨架，数据由前端异步加载
    return render_template("index.html", data=None)


@app.route("/api/status")
def api_status():
    """返回抓取进度状态（供前端进度条轮询）"""
    with _state_lock:
        st = dict(_load_state)
    elapsed = 0
    if st.get("started_at"):
        end = st.get("finished_at") or time.time()
        elapsed = round(end - st["started_at"], 1)
    st["elapsed"] = elapsed
    # 计算进度百分比
    pct = 0
    for s, label, p in STAGES:
        if st["stage"] == s:
            pct = p
            break
    if st["stage"] == "idle" and not st["active"] and app._home_cache is not None:
        pct = 100
        st["stage_label"] = "数据已缓存"
    st["progress"] = pct
    return jsonify(st)


@app.route("/api/home")
def api_home():
    """异步获取首页数据（触发首次抓取或读缓存）"""
    with _state_lock:
        if _load_state["active"]:
            return jsonify({"loading": True, "stage": _load_state["stage"]})

    if app._home_cache is not None and time.time() - app._home_ts < 300:
        return jsonify(app._home_cache)

    data = _load_home_data()
    app._home_cache = data
    app._home_ts = time.time()
    return jsonify(data)


@app.route("/api/refresh")
def api_refresh():
    """强制刷新首页数据"""
    crawler_api.clear_cache()
    data = _load_home_data()
    app._home_cache = data
    app._home_ts = time.time()
    return jsonify({"ok": True})


@app.route("/stock")
def stock_report():
    code = request.args.get("code", "").strip()
    if not code:
        return render_template("stock_report.html", data=None, error="请输入股票代码")
    try:
        stock_data = crawler_api.get_stock_posts(code)
        report = analysis_api.build_stock_report(stock_data)
        return render_template("stock_report.html", data=report, error=None)
    except Exception as e:
        return render_template("stock_report.html", data=None, error=str(e))


@app.route("/api/stock")
def api_stock():
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({"error": "请输入股票代码"}), 400
    try:
        stock_data = crawler_api.get_stock_posts(code, force=request.args.get("force") == "1")
        report = analysis_api.build_stock_report(stock_data)
        return jsonify(report)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "time": time.strftime("%Y-%m-%d %H:%M:%S")})


if __name__ == "__main__":
    import os
    app._home_cache = None
    app._home_ts = 0
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"股票舆情分析系统启动: http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
