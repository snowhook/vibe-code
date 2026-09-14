#!/usr/bin/env python3
"""A股市场情绪日报 —— 服务端取数模块（含自动修复）
数据源:
  ① 龙虎榜   东财数据中心 RPT_DAILYBILLBOARD_DETAILSNEW（支持历史日期）
  ② 行业板块  东财 push2delay 行业板块指数（实时，多host自动切换）
  ③ 题材热度  东财概念板块排行（实时；同花顺 getharden 已失效，此为自动修复替代源）
"""
import json
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

EM_DC = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
BOARD_HOSTS = [
    'https://push2delay.eastmoney.com',
    'https://push2.eastmoney.com',
    'https://17.push2.eastmoney.com',
    'https://90.push2.eastmoney.com',
]
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'


def _get_json(url, timeout=15, retries=2):
    last = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': UA,
                'Referer': 'https://quote.eastmoney.com/',
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            last = e
    raise last


def fetch_billboard(date_str):
    """指定交易日龙虎榜全量（分页）"""
    rows, page = [], 1
    while True:
        d = _get_json(EM_DC + '?' + urllib.parse.urlencode({
            'reportName': 'RPT_DAILYBILLBOARD_DETAILSNEW',
            'columns': 'ALL',
            'filter': f"(TRADE_DATE='{date_str}')",
            'pageSize': 500, 'pageNumber': page,
            'sortColumns': 'BILLBOARD_NET_AMT', 'sortTypes': '-1',
        }), timeout=25)
        res = d.get('result') or {}
        batch = res.get('data') or []
        rows.extend(batch)
        if page >= res.get('pages', 1) or not batch:
            break
        page += 1
    return rows


def _fetch_board(fs, pz=600):
    """板块行情，多 host 自动切换（自动修复 502/超时）"""
    err = None
    for host in BOARD_HOSTS:
        try:
            d = _get_json(host + '/api/qt/clist/get?' + urllib.parse.urlencode({
                'pn': 1, 'pz': pz, 'po': 1, 'np': 1, 'fltt': 2, 'invt': 2,
                'fid': 'f3', 'fs': fs,
                'fields': 'f3,f12,f14,f104,f105',
            }), timeout=12, retries=1)
            data = (d.get('data') or {}).get('diff')
            if data:
                return data, host
        except Exception as e:
            err = e
            continue
    raise err or RuntimeError('所有板块接口均不可用')


def fetch_industries():
    """东财行业板块（申万口径 一级+二级），按涨跌幅降序"""
    diff, host = _fetch_board('m:90+t:2')
    out = []
    for x in diff:
        if not isinstance(x.get('f3'), (int, float)):
            continue
        out.append({'name': x.get('f14', ''), 'pct': x['f3'], 'code': x.get('f12', '')})
    out.sort(key=lambda r: -r['pct'])
    return out, host


def fetch_concepts(top=20):
    """东财概念板块涨幅榜（题材热度，替代同花顺 getharden）"""
    diff, host = _fetch_board('m:90+t:3')
    out = []
    for x in diff:
        if not isinstance(x.get('f3'), (int, float)):
            continue
        out.append({
            'name': x.get('f14', ''), 'pct': x['f3'], 'code': x.get('f12', ''),
            'upCount': x.get('f104') or 0, 'downCount': x.get('f105') or 0,
        })
    out.sort(key=lambda r: -r['pct'])
    return out[:top], host


WEEK_CN = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']


def build_data(date_str):
    """汇总情绪日报数据。
    自动修复：所选日期无龙虎榜数据（非交易日）时，自动向前回退最多10天
    寻找最近交易日，并在 note 中说明。
    """
    warnings = []
    base = datetime.strptime(date_str, '%Y-%m-%d')
    eff = base
    rows = []
    for back in range(0, 11):
        d = base - timedelta(days=back)
        ds = d.strftime('%Y-%m-%d')
        try:
            rows = fetch_billboard(ds)
        except Exception as e:
            raise RuntimeError(f'龙虎榜接口错误: {e}')
        if rows:
            eff = d
            if back > 0:
                warnings.append(f'{date_str} 无龙虎榜数据（非交易日/数据未出），已自动切换到最近交易日 {ds}')
            break
    if not rows:
        raise RuntimeError('近10个交易日均无龙虎榜数据，接口可能异常')

    billboard = []
    for r in rows:
        billboard.append({
            'code': r.get('SECURITY_CODE', ''),
            'name': r.get('SECURITY_NAME_ABBR', ''),
            'date': (r.get('TRADE_DATE') or '')[:10],
            'close': r.get('CLOSE_PRICE'),
            'chg': r.get('CHANGE_RATE'),
            'net': r.get('BILLBOARD_NET_AMT') or 0,        # 元
            'buy': r.get('BILLBOARD_BUY_AMT') or 0,
            'sell': r.get('BILLBOARD_SELL_AMT') or 0,
            'turnover': r.get('TURNOVERRATE'),
            'reason': r.get('EXPLANATION', ''),
        })
    billboard.sort(key=lambda x: -x['net'])

    industries, ind_host = [], ''
    try:
        industries, ind_host = fetch_industries()
    except Exception as e:
        warnings.append(f'行业板块数据暂不可用（{e}），已跳过')

    concepts = []
    try:
        concepts, _ = fetch_concepts(20)
    except Exception as e:
        warnings.append(f'概念板块数据暂不可用（{e}），已跳过')

    today = datetime.now().strftime('%Y-%m-%d')
    if eff.strftime('%Y-%m-%d') != today and (industries or concepts):
        warnings.append('行业/概念板块为实时数据（仅当日），历史日期仅龙虎榜可回查')

    return {
        'date': eff.strftime('%Y-%m-%d'),
        'askedDate': date_str,
        'weekday': WEEK_CN[eff.weekday()],
        'generatedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'billboard': billboard,
        'industries': industries,
        'concepts': concepts,
        'warnings': warnings,
    }
