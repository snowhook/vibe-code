# -*- coding: utf-8 -*-
"""fetcher.py — 开奖数据抓取

官方源（福彩 cwl.gov.cn / 体彩 sporttery.cn）在国内网络可直连；
境外服务器 IP 会被 WAF 拦截（福彩 403 / 体彩 567），此时自动降级到
500.com 镜像（kaijiang XML / datachart 走势图），已实测境外可达。
"""
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
PAGE_SIZE = 30
SLEEP = 0.6
TIMEOUT = 25

FETCH = {
    'ssq': dict(src='cwl', code='ssq', kind='balls', z1=6, z2=1),
    '3d':  dict(src='cwl', code='3d',  kind='digit', pos=3),
    'qlc': dict(src='cwl', code='qlc', kind='balls', z1=7, z2=1),
    'kl8': dict(src='cwl', code='kl8', kind='balls', z1=20, z2=0),
    'dlt': dict(src='tc',  code='85',  kind='balls', z1=5, z2=2),
    'pl3': dict(src='tc',  code='35',  kind='digit', pos=3),
    'pl5': dict(src='tc',  code='350133', kind='digit', pos=5),
    'qxc': dict(src='tc',  code='04',  kind='digit', pos=6, special=True),
}

# ── 镜像源（境外可达）─────────────────────────────────────────
# 期号规范：福彩系近年在库存 7 位（2026116），体彩系 5 位（26116）。
# 镜像 XML 里双色球/七乐彩给 5 位，需补 '20'；3D/快乐8 本身给 7 位。
ISSUE_LEN = {'ssq': 7, 'qlc': 7, 'kl8': 7, '3d': 7,
             'dlt': 5, 'pl3': 5, 'pl5': 5, 'qxc': 5}

MIRROR_XML = {'ssq': 'ssq', 'dlt': 'dlt', '3d': 'sd', 'pl3': 'pls',
              'pl5': 'plw', 'qxc': 'qxc', 'qlc': 'qlc', 'kl8': 'kl8'}

MIRROR_XML_URL = 'https://kaijiang.500.com/static/info/kaijiang/xml/%s/list.xml'
MIRROR_XML_REF = 'https://kaijiang.500.com/'

# 走势图 HTML（datachart.500.com/history/{page}/history.php?limit=N）
# 体积 4~30KB、约 0.5 秒，远快于 XML 全量（0.3~1MB）。
# 列布局已逐一与库内官方数据核对：
#   ssq 16 列 [期, 红×6, 蓝, 空, 奖池, 一等注, 一等额, 二等注, 二等额, 销售额, 日]
#   dlt 15 列 [期, 前×5, 后×2, 奖池, 一等注, 一等额, 二等注, 二等额, 销售额, 日]
#   3d  11 列 [期, "2 8 0", ?, 销售额, 一等注, 直选额, ...]
#   pl3 11 列 [期, "6 2 2", ?, 销售额, 一等注, 一等额, 二等注, 二等额, ...]
#   pl5  5 列 [期, "6 2 2 4 6", ?, 销售额, 日]
#   qxc  5 列 [期, "8 7 1 5 7 0 6", ?, 销售额, 日]
#   qlc  6 列 [期, "01 08 ... 23", ?, 销售额, 奖池, 日]
# 快乐8 无走势图，走 XML。
CHART_SPEC = {
    'ssq': dict(page='newinc', mode='cols', num_from=1, sales=14, pool=9,
                c1=10, a1=11, c2=12, a2=13),
    'dlt': dict(page='newinc', mode='cols', num_from=1, sales=13, pool=8,
                c1=9, a1=10, c2=11, a2=12),
    '3d':  dict(page='inc', mode='str', num_col=1, sales=3, c1=4),
    'pl3': dict(page='inc', mode='str', num_col=1, sales=3, c1=4, a1=5, c2=6, a2=7),
    'pl5': dict(page='inc', mode='str', num_col=1, sales=3),
    'qxc': dict(page='inc', mode='str', num_col=1, sales=3),
    'qlc': dict(page='newinc', mode='str', num_col=1, sales=3, pool=4),
}
MIRROR_CHART_URL = 'https://datachart.500.com/%s/history/%s/history.php?limit=%d'




def _get(url, referer, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Referer': referer})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _json(url, referer):
    return json.loads(_get(url, referer).decode('utf-8', 'replace'))


def _money(s):
    if s is None:
        return None
    s = str(s).replace(',', '').strip()
    if s in ('', '---', '-1', 'null', 'None'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _int(s):
    try:
        return int(float(str(s).replace(',', '').strip()))
    except Exception:
        return 0


def _toks(s):
    """'01,02' / '01 02' / '280' -> ['01','02'] 之类"""
    s = str(s or '').strip()
    if not s:
        return []
    if ',' in s:
        return [x.strip() for x in s.split(',') if x.strip()]
    if ' ' in s:
        return [x for x in s.split() if x]
    return list(s)


def _norm(nums, pad=None):
    """规范化为「无前导零的整数」逗号串"""
    out = []
    for x in nums:
        try:
            out.append(int(x))
        except Exception:
            pass
    return ','.join(str(n) for n in out)


def _prize_cwl(grades):
    out = {}
    for g in grades or []:
        lv = str(g.get('type') or '').strip()
        if not lv:
            continue
        out[lv] = {'money': _money(g.get('typemoney')) or 0,
                   'count': _int(g.get('typenum'))}
    return json.dumps(out, ensure_ascii=False) if out else ''


def _prize_tc(levels):
    out = {}
    for g in levels or []:
        lv = str(g.get('prizeLevel') or '').strip()
        if not lv:
            continue
        cc = g.get('stakeCount')
        mm = g.get('stakeAmount')
        if (cc in (None, '') or mm in (None, '')) and g.get('prizeLevelList'):
            pass
        out[lv] = {'money': _money(mm) or 0, 'count': _int(cc)}
    return json.dumps(out, ensure_ascii=False) if out else ''


def fetch_cwl(lottery, latest_local=None, max_pages=4):
    cfg = FETCH[lottery]
    referer = 'https://www.cwl.gov.cn/ygkj/wqkjgg/'
    base = ('https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice'
            '?name=%s&pageNo=%%d&pageSize=%d&systemType=PC' % (cfg['code'], PAGE_SIZE))
    rows, seen, stop = [], 0, False
    for page in range(1, max_pages + 1):
        data = _json(base % page, referer)
        result = data.get('result') or []
        if not result:
            break
        for r in result:
            issue = str(r.get('code') or '').strip()
            if not issue:
                continue
            if latest_local and issue <= latest_local:
                stop = True
                break
            red = r.get('red') or ''
            blue = r.get('blue') or ''
            if cfg['kind'] == 'digit':
                z1 = _norm(_toks(red))
                z2 = ''
            else:
                toks1 = _toks(red)
                z1 = _norm(toks1[:cfg['z1']])
                z2 = _norm(_toks(blue)) if cfg.get('z2') else ''
            rows.append((lottery, issue, str(r.get('date') or '').split('(')[0],
                         z1, z2, _money(r.get('sales')) or 0, _money(r.get('poolmoney')) or 0,
                         _int(next((g.get('typenum') for g in (r.get('prizegrades') or [])
                                    if str(g.get('type')) == '1'), 0)),
                         _money(next((g.get('typemoney') for g in (r.get('prizegrades') or [])
                                      if str(g.get('type')) == '1'), None)) or 0,
                         _prize_cwl(r.get('prizegrades')),
                         time.strftime('%Y-%m-%d %H:%M:%S')))
            seen += 1
        if stop:
            break
        if page < max_pages:
            time.sleep(SLEEP)
    return rows


def fetch_tc(lottery, latest_local=None, max_pages=4):
    cfg = FETCH[lottery]
    referer = 'https://www.lottery.gov.cn/'
    base = ('https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry'
            '?gameNo=%s&provinceId=0&pageSize=%d&isVerify=1&pageNo=%%d' % (cfg['code'], PAGE_SIZE))
    rows, stop = [], False
    for page in range(1, max_pages + 1):
        data = _json(base % page, referer)
        value = data.get('value') or {}
        lst = value.get('list') or []
        if not lst:
            break
        for r in lst:
            issue = str(r.get('lotteryDrawNum') or '').strip()
            if not issue:
                continue
            if latest_local and issue <= latest_local:
                stop = True
                break
            nums = _toks(r.get('lotteryDrawResult'))
            if cfg['kind'] == 'balls':
                need = cfg['z1'] + cfg.get('z2', 0)
                if len(nums) < need:
                    continue
                z1, z2 = _norm(nums[:cfg['z1']]), _norm(nums[cfg['z1']:need])
            else:
                if cfg.get('special'):
                    if len(nums) < cfg['pos'] + 1:
                        continue
                    z1, z2 = _norm(nums[:cfg['pos']]), _norm([nums[cfg['pos']]])
                else:
                    if len(nums) < cfg['pos']:
                        continue
                    z1, z2 = _norm(nums[:cfg['pos']]), ''
            plist = r.get('prizeLevelList') or []
            p1 = next((g for g in plist if str(g.get('prizeLevel') or '') in ('一等奖', '直选')), {})
            rows.append((lottery, issue, str(r.get('lotteryDrawTime') or '')[:10],
                         z1, z2, 0, 0, _int(p1.get('stakeCount')),
                         _money(p1.get('stakeAmount')) or 0,
                         _prize_tc(plist),
                         time.strftime('%Y-%m-%d %H:%M:%S')))
        if stop:
            break
        if page < max_pages:
            time.sleep(SLEEP)
    return rows


# ───────────────────────── 镜像源 ─────────────────────────

def _zone_counts(cfg):
    """从 FETCH 配置推导 (zone1 位数, zone2 位数)

    球型直接给 z1/z2；数字型给 pos（+special 表示附加一位特别号）。
    """
    if 'z1' in cfg:
        return cfg['z1'], cfg.get('z2', 0)
    p = cfg['pos']
    return p, (1 if cfg.get('special') else 0)


def _fix_issue(expect, lottery):
    """把镜像期号规范成库存格式（双色球/七乐彩 5 位 -> 7 位）"""
    e = str(expect or '').strip()
    if not e.isdigit():
        return e
    want = ISSUE_LEN.get(lottery, len(e))
    if len(e) < want and want == 7:
        return '20' + e
    return e


def _toks_ball(s):
    """镜像源号码切分：逗号/空格分隔；未分隔时整体视作「一个号码」。

    注意不能复用官方源的 _toks：官方 cwl 数字型给的是紧凑串（'280' -> 拆位），
    而镜像给的是分隔串，未分隔的 '23' 是「一个 23 号」而非「2 和 3」。
    """
    s = str(s or '').strip()
    if not s:
        return []
    if ',' in s:
        return [x.strip() for x in s.split(',') if x.strip()]
    if ' ' in s:
        return [x for x in s.split() if x]
    return [s]


def _split_opencode(cfg, op):
    """镜像开奖号码串 -> (zone1, zone2)

    '红|蓝' 竖线分隔；无竖线时按彩种规则切分（数字型取 pos 位，快乐8 取全部）。
    """
    op = str(op or '').strip()
    if not op:
        return '', ''
    if '|' in op:
        a, b = op.split('|', 1)
        z1 = _norm(_toks_ball(a))
        z2 = _norm(_toks_ball(b)) if cfg.get('z2') else ''
        return z1, z2
    toks = _toks_ball(op)
    if cfg['kind'] == 'digit':
        p = cfg['pos']
        if cfg.get('special'):
            if len(toks) < p + 1:
                return '', ''
            return _norm(toks[:p]), _norm(toks[p:p + 1])
        if len(toks) < p:
            return '', ''
        return _norm(toks[:p]), ''
    n1, n2 = _zone_counts(cfg)
    need = n1 + n2
    if len(toks) < need:
        return '', ''
    z1 = _norm(toks[:n1])
    z2 = _norm(toks[n1:need]) if n2 else ''
    return z1, z2


def fetch_mirror_xml(lottery, latest_local=None, max_pages=4):
    """500.com 开奖 XML：仅号码（无销售额/奖池/奖级表）"""
    cfg = FETCH[lottery]
    slug = MIRROR_XML.get(lottery)
    if not slug:
        raise ValueError('镜像源不支持 %s' % lottery)
    raw = _get(MIRROR_XML_URL % slug, MIRROR_XML_REF)
    root = ET.fromstring(raw)
    elems = root.findall('row')
    if not elems:
        raise RuntimeError('镜像 XML 无数据行（可能被拦截或结构变更）')
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    rows = []
    for r in elems:
        issue = _fix_issue(r.get('expect'), lottery)
        if not issue:
            continue
        if latest_local and issue <= latest_local:
            continue
        z1, z2 = _split_opencode(cfg, r.get('opencode'))
        if not z1:
            continue
        rows.append((lottery, issue, (r.get('opentime') or '')[:10], z1, z2,
                     0, 0, 0, 0, '', ts))
    rows.sort(key=lambda x: x[1])
    return rows


def fetch_mirror_chart(lottery, latest_local=None, max_pages=4):
    """500.com 走势图 HTML：号码 + 销售额（部分彩种含奖池/一二等奖）"""
    cfg = FETCH[lottery]
    spec = CHART_SPEC.get(lottery)
    if not spec:
        raise ValueError('走势图源不支持 %s' % lottery)
    n1, n2 = _zone_counts(cfg)
    n_total = n1 + n2
    slug = MIRROR_XML[lottery]      # 站点路径（3d->sd, pl3->pls, pl5->plw）
    limit = max(30, int(max_pages) * 30)
    url = MIRROR_CHART_URL % (slug, spec['page'], limit)
    raw = _get(url, 'https://datachart.500.com/%s/' % slug).decode('utf-8', 'replace')

    need = max([v for k, v in spec.items() if k not in ('page', 'mode')] or [0])
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    rows, matched = [], 0
    for m in re.finditer(r'<tr class="t_tr1">(.*?)</tr>', raw, re.S):
        matched += 1
        body = re.sub(r'<!--.*?-->', '', m.group(1), flags=re.S)  # 剔除注释占位列
        tds = [re.sub(r'<[^>]*>', '', t).replace('&nbsp;', '').strip()
               for t in re.findall(r'<td[^>]*>(.*?)</td>', body, re.S)]
        if len(tds) <= need:
            continue
        issue = _fix_issue(tds[0], lottery)
        if not issue:
            continue
        if latest_local and issue <= latest_local:
            continue
        if spec['mode'] == 'cols':
            nums = tds[spec['num_from']:spec['num_from'] + n_total]
        else:
            nums = _toks_ball(tds[spec['num_col']].replace(' ', ','))
        nums = [x for x in nums if x and x.replace(',', '').isdigit()]
        if len(nums) < n_total:
            continue
        z1 = _norm(nums[:n1])
        z2 = _norm(nums[n1:n_total]) if n2 else ''
        g = lambda k: _money(tds[spec[k]]) if k in spec else None  # noqa: E731
        sales = g('sales') or 0
        pool = g('pool') or 0
        c1, a1 = g('c1') or 0, g('a1') or 0
        c2, a2 = g('c2') or 0, g('a2') or 0
        pt = {}
        if c1 or a1:
            pt['1'] = {'money': a1, 'count': int(c1)}
        if c2 or a2:
            pt['2'] = {'money': a2, 'count': int(c2)}
        rows.append((lottery, issue, tds[-1], z1, z2, sales, pool,
                     int(c1), a1,
                     json.dumps(pt, ensure_ascii=False) if pt else '', ts))
    if not matched:
        raise RuntimeError('走势图页面结构变化：未匹配到数据行')
    rows.sort(key=lambda x: x[1])
    return rows


def fetch_mirror(lottery, latest_local=None, max_pages=4):
    """镜像入口：有走势图的走走势图（快，含销售额），否则走 XML"""
    if lottery in CHART_SPEC:
        try:
            return fetch_mirror_chart(lottery, latest_local, max_pages)
        except Exception:
            pass
    return fetch_mirror_xml(lottery, latest_local, max_pages)


# ───────────────────────── 统一入口 ─────────────────────────

def fetch_official(lottery, latest_local=None, max_pages=4):
    cfg = FETCH[lottery]
    if cfg['src'] == 'cwl':
        return fetch_cwl(lottery, latest_local, max_pages)
    return fetch_tc(lottery, latest_local, max_pages)


def fetch(lottery, latest_local=None, max_pages=4, source='auto'):
    """抓取开奖数据。

    返回 (rows, 来源标签)；来源标签为 'official' 或 'mirror'。
    source: 'auto' 官方优先、失败降级镜像 / 'official' / 'mirror'
    """
    cfg = FETCH.get(lottery)
    if not cfg:
        raise ValueError('未知彩种 %s' % lottery)
    if source == 'official':
        plan = [('official', fetch_official)]
    elif source == 'mirror':
        plan = [('mirror', fetch_mirror)]
    else:
        plan = [('official', fetch_official), ('mirror', fetch_mirror)]
    last = None
    for tag, fn in plan:
        try:
            return fn(lottery, latest_local, max_pages), tag
        except Exception as e:
            last = e
    raise last if last else RuntimeError('fetch failed')
