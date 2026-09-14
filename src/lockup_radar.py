#!/usr/bin/env python3
"""A股限售解禁预警雷达 —— 服务端取数模块
数据源:
  - 东方财富数据中心 RPT_LIFT_STAGE   限售解禁日历
  - 东方财富数据中心 RPT_SHAREBONUS_DET 分红送转
  - 腾讯财经行情     qt.gtimg.cn
"""
import json
import re
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

EM_API = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'


def _em_get(params, timeout=25):
    url = EM_API + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def fetch_lockup_window(base_date, days=90):
    """拉取 [base, base+days] 内全部解禁批次（分页抓全）"""
    end_date = (base_date + timedelta(days=days)).strftime('%Y-%m-%d')
    rows, page = [], 1
    while True:
        d = _em_get({
            'reportName': 'RPT_LIFT_STAGE', 'columns': 'ALL',
            'filter': f"(FREE_DATE>='{base_date.strftime('%Y-%m-%d')}')(FREE_DATE<='{end_date}')",
            'pageSize': 500, 'pageNumber': page,
            'sortColumns': 'LIFT_MARKET_CAP', 'sortTypes': '-1',
        })
        res = d.get('result') or {}
        batch = res.get('data') or []
        rows.extend(batch)
        if page >= res.get('pages', 1) or not batch:
            break
        page += 1
    return rows


def fetch_history(code, limit=6):
    """个股历史解禁（基准日之前，按日期倒序）"""
    d = _em_get({
        'reportName': 'RPT_LIFT_STAGE', 'columns': 'ALL',
        'filter': f"(SECURITY_CODE=\"{code}\")(FREE_DATE<'{datetime.now().strftime('%Y-%m-%d')}')",
        'pageSize': limit, 'sortColumns': 'FREE_DATE', 'sortTypes': '-1',
    })
    return ((d.get('result') or {}).get('data')) or []


def fetch_future(code, base_date, limit=5):
    """个股未来解禁排期（基准日之后，近2年内）"""
    horizon = (base_date + timedelta(days=730)).strftime('%Y-%m-%d')
    d = _em_get({
        'reportName': 'RPT_LIFT_STAGE', 'columns': 'ALL',
        'filter': f"(SECURITY_CODE=\"{code}\")(FREE_DATE>'{base_date.strftime('%Y-%m-%d')}')(FREE_DATE<='{horizon}')",
        'pageSize': limit, 'sortColumns': 'FREE_DATE', 'sortTypes': '1',
    })
    return ((d.get('result') or {}).get('data')) or []


def fetch_dividend(code, limit=4):
    """个股最近分红"""
    try:
        d = _em_get({
            'reportName': 'RPT_SHAREBONUS_DET', 'columns': 'ALL',
            'filter': f"(SECURITY_CODE=\"{code}\")(ASSIGN_PROGRESS=\"实施分配\")",
            'pageSize': limit, 'sortColumns': 'EX_DIVIDEND_DATE', 'sortTypes': '-1',
        })
        return ((d.get('result') or {}).get('data')) or []
    except Exception:
        return []


def fetch_quotes(codes):
    """腾讯批量行情 -> {code: {price, pct}}"""
    out = {}
    syms = []
    for c in codes:
        if c.startswith('6'):
            syms.append('sh' + c)
        elif c.startswith(('0', '3')):
            syms.append('sz' + c)
        elif c.startswith(('4', '8')):
            syms.append('bj' + c)
        else:
            syms.append('sh' + c)
    for i in range(0, len(syms), 60):
        chunk = syms[i:i + 60]
        url = 'https://qt.gtimg.cn/q=' + ','.join(chunk)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=12) as r:
                txt = r.read().decode('gbk', 'ignore')
            for m in re.finditer(r'v_(?:sh|sz|bj)(\d{6})="([^"]*)"', txt):
                code, seg = m.group(1), m.group(2)
                f = seg.split('~')
                if len(f) > 32:
                    try:
                        out[code] = {'price': float(f[3]), 'pct': float(f[32])}
                    except (ValueError, IndexError):
                        pass
        except Exception:
            continue
    return out


def build_data(base_date, days=90):
    """汇总全量数据 -> 前端渲染用 JSON"""
    base_date = datetime.strptime(base_date, '%Y-%m-%d')
    rows = fetch_lockup_window(base_date, days)

    # 规范化每批次
    items = []
    for r in rows:
        try:
            fd = r.get('FREE_DATE', '')[:10]
            shares = r.get('CURRENT_FREE_SHARES') or 0      # 万股
            mcap = r.get('LIFT_MARKET_CAP') or 0            # 万元
            total_ratio = (r.get('TOTAL_RATIO') or 0)       # 占总股本
            free_ratio = (r.get('FREE_RATIO') or 0)         # 占流通盘
            items.append({
                'date': fd,
                'code': r.get('SECURITY_CODE', ''),
                'name': r.get('SECURITY_NAME_ABBR', ''),
                'type': r.get('FREE_SHARES_TYPE', '') or '其他',
                'holders': r.get('BATCH_HOLDER_NUM') or 0,
                'shares': shares, 'mcap': mcap,
                'totalRatio': total_ratio * 100,
                'freeRatio': free_ratio * 100,
                'new': r.get('NEW'),
            })
        except Exception:
            continue
    items.sort(key=lambda x: -x['mcap'])

    codes = sorted({x['code'] for x in items})
    quotes = fetch_quotes(codes)
    for x in items:
        q = quotes.get(x['code'])
        x['price'] = q['price'] if q else None
        x['pct'] = q['pct'] if q else None

    # 代表股: 市值 TOP3
    reps = []
    seen = set()
    for x in items:
        if x['code'] in seen:
            continue
        seen.add(x['code'])
        hist = fetch_history(x['code'])
        fut = fetch_future(x['code'], base_date)
        divs = fetch_dividend(x['code'])
        reps.append({
            'item': x,
            'history': [{
                'date': (h.get('FREE_DATE') or '')[:10],
                'type': h.get('FREE_SHARES_TYPE', ''),
                'shares': h.get('CURRENT_FREE_SHARES') or 0,
                'mcap': h.get('LIFT_MARKET_CAP') or 0,
                'totalRatio': (h.get('TOTAL_RATIO') or 0) * 100,
            } for h in hist],
            'future': [{
                'date': (h.get('FREE_DATE') or '')[:10],
                'type': h.get('FREE_SHARES_TYPE', ''),
                'shares': h.get('CURRENT_FREE_SHARES') or 0,
                'mcap': h.get('LIFT_MARKET_CAP') or 0,
                'totalRatio': (h.get('TOTAL_RATIO') or 0) * 100,
            } for h in fut if (h.get('FREE_DATE') or '')[:10] != x['date']],
            'dividends': [{
                'date': (h.get('EX_DIVIDEND_DATE') or '')[:10],
                'bonus': h.get('PRETAX_BONUS_RMB'),
                'profile': h.get('IMPL_PLAN_PROFILE', ''),
            } for h in divs],
        })
        if len(reps) >= 3:
            break

    return {
        'base': base_date.strftime('%Y-%m-%d'),
        'end': (base_date + timedelta(days=days)).strftime('%Y-%m-%d'),
        'days': days,
        'generatedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'items': items,
        'reps': reps,
    }
