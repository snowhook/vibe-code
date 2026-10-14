# -*- coding: utf-8 -*-
"""games.py — 彩种定义 / 玩法 / 注数 / 中奖概率（纯标准库）

设计：
- type='zone'  : 分区选号型（双色球、大乐透、七乐彩、快乐8）
- type='digit' : 按位选号型（3D、排列3、排列5、七星彩）
- 复式：某分区选 k 个号（k>=count）时，注数按组合数展开
中奖判定统一走 hit() -> (层级号, 层级名, 奖金) ；奖金 None 表示浮动（奖池分摊）
"""
import math

FLOAT = None


def C(n, k):
    if k < 0 or k > n:
        return 0
    return math.comb(n, k)


# ───────────────────────── 奖级判定 ─────────────────────────

def _ssq_prize(h):
    r, b = h
    if r == 6 and b == 1: return (1, '一等奖', FLOAT)
    if r == 6 and b == 0: return (2, '二等奖', FLOAT)
    if r == 5 and b == 1: return (3, '三等奖', 3000)
    if (r == 5 and b == 0) or (r == 4 and b == 1): return (4, '四等奖', 200)
    if (r == 4 and b == 0) or (r == 3 and b == 1): return (5, '五等奖', 10)
    if b == 1 and r <= 2: return (6, '六等奖', 5)
    return None


def _dlt_prize(h):
    f, b = h
    if f == 5 and b == 2: return (1, '一等奖', FLOAT)
    if f == 5 and b == 1: return (2, '二等奖', FLOAT)
    if f == 5 and b == 0: return (3, '三等奖', 10000)
    if f == 4 and b == 2: return (4, '四等奖', 3000)
    if f == 4 and b == 1: return (5, '五等奖', 300)
    if f == 3 and b == 2: return (6, '六等奖', 200)
    if f == 4 and b == 0: return (7, '七等奖', 100)
    if (f == 3 and b == 1) or (f == 2 and b == 2): return (8, '八等奖', 15)
    if (f == 3 and b == 0) or (f == 1 and b == 2) or (f == 2 and b == 1) or (f == 0 and b == 2):
        return (9, '九等奖', 5)
    return None


def _qlc_prize(h):
    # 30 选 7，开出 7 个基本号 + 1 个特别号；h = (命中基本号数, 是否命中特别号)
    r, s = h
    if r == 7: return (1, '一等奖', FLOAT)
    if r == 6 and s == 1: return (2, '二等奖', FLOAT)
    if r == 6 and s == 0: return (3, '三等奖', 200)
    if r == 5 and s == 1: return (4, '四等奖', 50)
    if r == 5 and s == 0: return (5, '五等奖', 10)
    if r == 4 and s == 1: return (6, '六等奖', 10)
    if (r == 4 and s == 0) or (r == 3 and s == 1): return (7, '七等奖', 5)
    return None


_KL8_TABLE = {
    10: {10: 5000000, 9: 8000, 8: 800, 7: 80, 6: 5, 5: 3, 0: 2},
    9:  {9: 300000, 8: 2000, 7: 200, 6: 20, 5: 5, 4: 3, 0: 2},
    8:  {8: 50000, 7: 800, 6: 88, 5: 10, 4: 3, 0: 2},
    7:  {7: 10000, 6: 288, 5: 28, 4: 4, 0: 2},
    6:  {6: 3000, 5: 30, 4: 10, 3: 3},
    5:  {5: 1000, 4: 21, 3: 3},
    4:  {4: 100, 3: 5, 2: 3},
    3:  {3: 53, 2: 3},
    2:  {2: 19},
    1:  {1: 4.6},
}


def _kl8_prize_factory(k):
    def fn(h):
        m = h[0]
        amt = _KL8_TABLE.get(k, {}).get(m)
        if amt is None:
            return None
        return (m, '中%d' % m, amt)
    return fn


def _digit_direct_prize(fix):
    def fn(h):
        return (1, '直选', fix) if h[0] == 1 else None
    return fn


# ───────────────────────── 彩种定义 ─────────────────────────

Z = lambda key, name, count, lo, hi: dict(key=key, name=name, count=count, min=lo, max=hi)

GAMES = {}


def _add(code, name, org, gtype, zones, prizes, plays=None, note='', price=2):
    GAMES[code] = dict(code=code, name=name, org=org, type=gtype, zones=zones,
                       prizes=prizes, plays=plays or {'std': {'key': 'std', 'name': '标准'}},
                       note=note, price=price)


# 双色球 / 大乐透 / 七乐彩：分区型
_add('ssq', '双色球', '福彩', 'zone',
     [Z('r', '红球', 6, 1, 33), Z('b', '蓝球', 1, 1, 16)],
     _ssq_prize, note='红球 6/33，蓝球 1/16')

_add('dlt', '大乐透', '体彩', 'zone',
     [Z('f', '前区', 5, 1, 35), Z('b', '后区', 2, 1, 12)],
     _dlt_prize, note='前区 5/35，后区 2/12')

_add('qlc', '七乐彩', '福彩', 'zone',
     [Z('n', '基本号', 7, 1, 30)],
     _qlc_prize, note='30 选 7，另开 1 个特别号')

# 快乐8：选 1~10 个
kl8_prizes = {str(k): _kl8_prize_factory(k) for k in range(1, 11)}
_add('kl8', '快乐8', '福彩', 'zone',
     [Z('n', '选号', 10, 1, 80)],
     kl8_prizes['10'],
     plays={str(k): {'key': str(k), 'name': '选%s' % k} for k in range(1, 11)},
     note='从 80 个号中选 1~10 个，开出 20 个')

# 3D / 排列3：按位 + 组选
_digit_zones = [Z('a', '百位', 1, 0, 9), Z('b', '十位', 1, 0, 9), Z('c', '个位', 1, 0, 9)]

_add('3d', '福彩3D', '福彩', 'digit', _digit_zones,
     _digit_direct_prize(1040),
     plays={
         'direct': {'key': 'direct', 'name': '直选'},
         'g3': {'key': 'g3', 'name': '组选3'},
         'g6': {'key': 'g6', 'name': '组选6'},
     }, note='三位数，000~999')

_add('pl3', '排列3', '体彩', 'digit', _digit_zones,
     _digit_direct_prize(1040),
     plays={
         'direct': {'key': 'direct', 'name': '直选'},
         'g3': {'key': 'g3', 'name': '组选3'},
         'g6': {'key': 'g6', 'name': '组选6'},
     }, note='三位数，000~999')

_add('pl5', '排列5', '体彩', 'digit',
     [Z(k, n, 1, 0, 9) for k, n in zip('abcde', ['第1位', '第2位', '第3位', '第4位', '第5位'])],
     _digit_direct_prize(100000), note='五位数，00000~99999')

_add('qxc', '七星彩', '体彩', 'digit',
     [Z(k, n, 1, 0, 9) for k, n in zip('abcdef',
      ['第1位', '第2位', '第3位', '第4位', '第5位', '第6位'])] +
     [Z('g', '特别号', 1, 0, 14)],
     _digit_direct_prize(5000000), note='前六位 0~9，第七位特别号 0~14')


# ───────────────────────── 注数计算 ─────────────────────────

def _as_list(v):
    """按位型彩种的取号：开奖/历史票据可能是标量，选区/展开是列表 —— 一律按列表处理。
    只认一种就会炸在 len() 上（3D 直选出号的 500 就是这么来的）。"""
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return list(v)
    return [v]


def bet_count(code, play, selection):
    """selection: {zone_key: [号码...]}  返回注数"""
    g = GAMES.get(code)
    if not g:
        return 0
    if g['type'] == 'digit':
        if play in ('g3', 'g6'):
            nums = _as_list(selection.get('a') or selection.get('n'))
            k = 2 if play == 'g3' else 3
            return C(len(set(nums)), k)
        total = 1
        for z in g['zones']:
            picks = _as_list(selection.get(z['key']))
            if len(picks) < z['count']:
                return 0
            total *= C(len(picks), z['count'])
        return total
    # zone 型
    total = 1
    for z in g['zones']:
        picks = _as_list(selection.get(z['key']))
        need = z['count']
        if code == 'kl8':
            need = int(play) if str(play).isdigit() else z['count']
        if len(picks) < need:
            return 0
        total *= C(len(picks), need)
    return total


def combos(selection, zones, play=None, code=None):
    """展开为具体注列表（每注是 {zone_key: [号码...]}），注数过大时抛出"""
    import itertools
    out = []
    # 3D/排列3 组选：号码放在 'n' 里（不在 zones 的任何分位上），
    # 必须在这里单独展开成 C(k,2) / C(k,3) 注 —— 与 bet_count 的口径一致。
    if code in ('3d', 'pl3') and play in ('g3', 'g6'):
        nums = sorted(set(_as_list(selection.get('n') or selection.get('a'))))
        k = 2 if play == 'g3' else 3
        if len(nums) < k:
            return []
        return [{'n': list(c)} for c in itertools.combinations(nums, k)]
    per = []
    for z in zones:
        picks = sorted(set(_as_list(selection.get(z['key']))))
        need = z['count']
        if code == 'kl8' and play and str(play).isdigit():
            need = int(play)
        if len(picks) < need:
            return []
        per.append(list(itertools.combinations(picks, need)))
    for combo in itertools.product(*per):
        out.append({z['key']: list(c) for z, c in zip(zones, combo)})
        if len(out) > 200000:
            raise ValueError('注数过大')
    return out


# ───────────────────────── 概率 ─────────────────────────

def hit_distribution(code, play='std'):
    """返回 [(命中描述tuple, 概率, (层级,名称,奖金))]，仅含有奖的组合"""
    g = GAMES[code]
    res = []
    if code == 'ssq':
        T = C(33, 6) * C(16, 1)
        for r in range(7):
            for b in range(2):
                p = C(6, r) * C(27, 6 - r) * C(1, b) * C(15, 1 - b) / T
                pr = g['prizes']((r, b))
                if pr:
                    res.append(((r, b), p, pr))
    elif code == 'dlt':
        T = C(35, 5) * C(12, 2)
        for f in range(6):
            for b in range(3):
                p = C(5, f) * C(30, 5 - f) * C(2, b) * C(10, 2 - b) / T
                pr = g['prizes']((f, b))
                if pr:
                    res.append(((f, b), p, pr))
    elif code == 'qlc':
        T = C(30, 7)
        for r in range(8):
            for s in range(2):
                p = C(7, r) * C(1, s) * C(22, 7 - r - s) / T
                pr = g['prizes']((r, s))
                if pr:
                    res.append(((r, s), p, pr))
    elif code == 'kl8':
        k = int(play) if str(play).isdigit() else 10
        T = C(80, k)
        fn = _kl8_prize_factory(k)
        for m in range(k + 1):
            p = C(20, m) * C(60, k - m) / T
            pr = fn((m,))
            if pr:
                res.append(((m,), p, pr))
    elif code in ('3d', 'pl3'):
        if play == 'direct':
            res.append(((1,), 1 / 1000, (1, '直选', 1040)))
        elif play == 'g6':
            res.append(((1,), 6 / 1000, (1, '组选6', 173)))
        elif play == 'g3':
            res.append(((1,), 3 / 1000, (1, '组选3', 346)))
    elif code == 'pl5':
        res.append(((1,), 1 / 100000, (1, '直选', 100000)))
    elif code == 'qxc':
        res.append(((1,), 1 / 10000000, (1, '一等奖', FLOAT)))
    return res


def top_prize_prob(code, play='std'):
    """最高奖级（层级号最小）的概率"""
    d = hit_distribution(code, play)
    if not d:
        return 0.0
    return min(d, key=lambda x: x[2][0])[1]


def total_win_prob(code, play='std'):
    return sum(p for _, p, _ in hit_distribution(code, play))


def all_prob(code, play='std'):
    """任意奖级（含未中奖）的完整概率表"""
    return hit_distribution(code, play)


def _digit_num(v):
    """数字型彩种取单值：开奖数据给标量（to_draw），票据/picks 给长度 1 的列表（combos 展开）。

    两种形态都要吃 —— 只认一种就是「3D/排列3 中了也判不中」的老 bug。
    """
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    return v


def hit_level(code, play, draw, ticket):
    """计算单注命中层级。draw/ticket 均为 {zone_key:[...]}；数字型彩种兼容标量与列表"""
    g = GAMES[code]
    if code in ('3d', 'pl3'):
        d3 = [_digit_num(draw.get(k)) for k in ('a', 'b', 'c')]
        if play == 'direct':
            t3 = [_digit_num(ticket.get(k)) for k in ('a', 'b', 'c')]
            return (1, '直选', 1040) if t3 == d3 else None
        if play in ('g3', 'g6'):
            tn = ticket.get('n')
            if tn is None:                      # 兼容被 combos 展开成 a/b/c 的形态
                tn = [_digit_num(ticket.get(k)) for k in ('a', 'b', 'c')]
            t = sorted(set(x for x in tn if x is not None))
            ds = sorted(set(x for x in d3 if x is not None))
            # 组选比的是「不同数字的集合」：组选3 = 2 个不同数字（对子），组选6 = 3 个
            if play == 'g6':
                return (1, '组选6', 173) if len(ds) == 3 and t == ds else None
            return (1, '组选3', 346) if len(ds) == 2 and t == ds else None
    if g['type'] == 'digit':
        ok = all(_digit_num(ticket.get(z['key'])) == _digit_num(draw.get(z['key']))
                 for z in g['zones'])
        pr = g['prizes']((1 if ok else 0,))
        return pr
    if code == 'ssq':
        r = len(set(ticket['r']) & set(draw['r']))
        b = 1 if set(ticket['b']) & set(draw['b']) else 0
        return g['prizes']((r, b))
    if code == 'dlt':
        f = len(set(ticket['f']) & set(draw['f']))
        b = len(set(ticket['b']) & set(draw['b']))
        return g['prizes']((f, b))
    if code == 'qlc':
        tk = set(ticket['n'])
        r = len(tk & set(draw.get('n') or []))
        sp = draw.get('sp') or []
        s = 1 if sp and sp[0] in tk else 0
        return g['prizes']((r, s))
    if code == 'kl8':
        m = len(set(ticket['n']) & set(draw['n']))
        k = len(ticket['n'])
        return _kl8_prize_factory(k)((m,))
    return None


def prize_table(code):
    """奖级总览：层级 / 名称 / 单注概率 / 参考奖金"""
    rows = []
    seen = set()
    for play in GAMES[code]['plays']:
        for _, p, (lv, nm, amt) in hit_distribution(code, play):
            key = (play, lv)
            if key in seen:
                continue
            seen.add(key)
            rows.append(dict(play=play, play_name=GAMES[code]['plays'][play]['name'],
                             level=lv, name=nm, prob=p, amount=amt))
    return rows
