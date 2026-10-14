# -*- coding: utf-8 -*-
"""analyzer.py — 统计 / 遗漏 / 冷热 / 出号策略 / 概率建议"""
import random
from collections import Counter

import games as G


def _split(s):
    return [int(x) for x in str(s or '').split(',') if str(x).strip() != '']


def to_draw(code, row):
    """db 行 -> {zone_key: [号码]}（qlc 特别号放在 sp）"""
    z1, z2 = _split(row.get('zone1')), _split(row.get('zone2'))
    if code == 'ssq':
        return {'r': z1, 'b': z2}
    if code == 'dlt':
        return {'f': z1, 'b': z2}
    if code == 'qlc':
        return {'n': z1, 'sp': z2}
    if code == 'kl8':
        return {'n': z1}
    if code == 'pl5':
        return dict(zip('abcde', z1))
    if code == 'qxc':
        d = dict(zip('abcdef', z1))
        d['g'] = z2[0] if z2 else None
        return d
    if code in ('3d', 'pl3'):
        return dict(zip('abc', z1))
    return {'n': z1}


# ───────────────────────── 统计 ─────────────────────────

class Stats:
    def __init__(self, code, draws):
        self.code = code
        self.game = G.GAMES[code]
        self.draws = draws                       # 升序
        self.n = len(draws)
        self.seq = [to_draw(code, r) for r in draws]

    def _zone_nums(self, zkey):
        out = []
        for d in self.seq:
            v = d.get(zkey)
            if v:
                out.extend(v if isinstance(v, list) else [v])
        return out

    def freq(self, zkey=None):
        """某区（或全部）各号码出现次数"""
        c = Counter()
        if zkey:
            c.update(self._zone_nums(zkey))
        else:
            for z in self.game['zones']:
                if z['key'] != 'sp':
                    c.update(self._zone_nums(z['key']))
        return c

    def omission(self, zkey):
        """当前遗漏：某号码距上次出现多少期"""
        c = Counter()
        gaps = {}
        pool = range(self.game['zones'][[z['key'] for z in self.game['zones']].index(zkey)]['min'],
                     self.game['zones'][[z['key'] for z in self.game['zones']].index(zkey)]['max'] + 1)
        for n in pool:
            gaps[n] = None
        for i, d in enumerate(self.seq):
            v = d.get(zkey)
            if v is None:
                continue
            v = v if isinstance(v, list) else [v]
            for n in v:
                if n in gaps:
                    gaps[n] = i
        out = {}
        for n in pool:
            out[n] = (self.n - 1 - gaps[n]) if gaps[n] is not None else self.n
        return out

    def hot(self, zkey, k):
        c = self.freq(zkey)
        z = next(z for z in self.game['zones'] if z['key'] == zkey)
        pool = [n for n in range(z['min'], z['max'] + 1)]
        return sorted(pool, key=lambda n: (-c.get(n, 0), n))[:k]

    def cold(self, zkey, k):
        om = self.omission(zkey)
        return sorted(om, key=lambda n: (-om[n], n))[:k]

    def recent(self, k=30):
        return list(reversed(self.draws[-k:]))


# ───────────────────────── 反人群风险（大众图案化程度） ─────────────────────────

def crowd_risk(nums, mx=33):
    """数值越大 = 越像大众爱买的组合（连续号、生日区间扎堆等）"""
    if not nums:
        return 0.0
    nums = sorted(nums)
    r = 0.0
    if all(n <= 31 for n in nums):
        r += 0.35
    cons = sum(1 for a, b in zip(nums, nums[1:]) if b - a == 1)
    r += 0.12 * cons
    low = sum(1 for n in nums if n <= 12)
    if low >= 3:
        r += 0.2
    if nums[-1] - nums[0] <= 10:
        r += 0.15
    return round(min(r, 1.0), 2)


# ───────────────────────── 出号 ─────────────────────────

STRATEGIES = {
    'random':     {'name': '纯随机', 'desc': '真·机选，各号码等概率'},
    'balanced':   {'name': '冷热均衡', 'desc': '热号+冷号混合，仅为铺开覆盖面'},
    'hot':        {'name': '追热', 'desc': '偏向近期高频号'},
    'cold':       {'name': '追冷', 'desc': '偏向长期遗漏号'},
    'anti-crowd': {'name': '反人群', 'desc': '避开大众爱买的图案化组合（中了少分人）'},
}


def _weighted_pick(pool, weights, k, rng):
    """按权重不放回抽 k 个：权重越大越容易被抽中；权重全为 0 时退化为等概率。

    注意：这里**必须**用「按权重随机的逐个抽取」，不能直接取权重最高的前 k 个 ——
    取前 k 个是确定性的，同一批里出 N 注就会得到 N 组一模一样的号码（历史缺陷）。
    """
    cand = list(pool)
    w = [max(float(weights.get(n, 0) or 0), 0.0) for n in cand]
    if sum(w) <= 0:
        w = [1.0] * len(cand)
    out = []
    for _ in range(min(k, len(cand))):
        tot = sum(w)
        r = rng.random() * tot
        acc = 0.0
        idx = len(cand) - 1
        for i, wi in enumerate(w):
            acc += wi
            if r <= acc:
                idx = i
                break
        out.append(cand.pop(idx))
        w.pop(idx)
    return sorted(out)


def _pick_zone(z, strategy, stats, rng, k):
    pool = list(range(z['min'], z['max'] + 1))
    k = max(1, min(k, len(pool)))
    if strategy == 'random' or stats is None:
        return sorted(rng.sample(pool, k))
    if strategy == 'hot':
        c = stats.freq(z['key'])
        return _weighted_pick(pool, {n: (c.get(n, 0) + 1) ** 3 for n in pool}, k, rng)
    if strategy == 'cold':
        om = stats.omission(z['key'])
        return _weighted_pick(pool, {n: (om.get(n, 0) + 1) ** 3 for n in pool}, k, rng)
    # balanced（以及反人群的非主区）：热冷混抽
    hn = max(1, k // 2)
    hot = stats.hot(z['key'], min(hn + 2, len(pool)))
    s = set(rng.sample(hot, min(hn, len(hot))))
    while len(s) < k:
        s.add(rng.choice(pool))
    return sorted(s)


def generate(code, play='std', strategy='random', count=1, seed=None, stats=None,
             used=None, sizes=None):
    """返回 count 注： [{zone_key: [号码...]}, ...]

    sizes: 复式个数覆盖 {zone_key: n}（例 {'r': 8, 'b': 2}），缺省用彩种规定的单注个数。
           个数 > 规定个数即为复式，注数由 games.bet_count() 按组合数展开。
    同一批出号会做「整注去重」：N 注里不会出现两组完全相同的号码。
    """
    g = G.GAMES[code]
    rng = random.Random(seed)
    sizes = {k: int(v) for k, v in (sizes or {}).items() if v}
    used = set(used or [])
    out = []
    sigs = set()

    def need_of(z):
        """这一注在该分区要出几个号（含复式覆盖）"""
        if code == 'kl8':
            base = int(play) if str(play).isdigit() else z['count']
            return max(1, min(int(sizes.get('n') or base), z['max'] - z['min'] + 1))
        return max(1, min(int(sizes.get(z['key']) or z['count']), z['max'] - z['min'] + 1))

    def sig_of(t):
        return tuple(sorted((k, tuple(v if isinstance(v, list) else [v]))
                            for k, v in t.items()))

    if g['type'] == 'digit':
        grp = play in ('g3', 'g6')
        for _ in range(count):
            t = None
            for _try in range(40):
                if grp:
                    lo_k = 2 if play == 'g3' else 3
                    k = max(lo_k, min(int(sizes.get('n') or lo_k), 10))
                    pool = list(range(10))
                    if strategy == 'hot' and stats:
                        c = stats.freq('a')
                        nums = _weighted_pick(pool, {n: (c.get(n, 0) + 1) ** 3 for n in pool}, k, rng)
                    elif strategy == 'cold' and stats:
                        om = stats.omission('a')
                        nums = _weighted_pick(pool, {n: (om.get(n, 0) + 1) ** 3 for n in pool}, k, rng)
                    else:
                        nums = sorted(rng.sample(pool, k))
                    cand = {'n': nums}
                else:
                    cand = {}
                    # 按位直选：每位 1 个数字。一律存成长度 1 的**列表** ——
                    # 与前端选区 / 票据归一化 / bet_count / hit_level 的形态保持一致，
                    # 存标量会让下游 len() / 渲染 / 注数计算全线出错。
                    for z in g['zones']:
                        pool = list(range(z['min'], z['max'] + 1))
                        if strategy == 'hot' and stats:
                            c = stats.freq(z['key'])
                            cand[z['key']] = [_weighted_pick(
                                pool, {n: (c.get(n, 0) + 1) ** 3 for n in pool}, 1, rng)[0]]
                        elif strategy == 'cold' and stats:
                            om = stats.omission(z['key'])
                            cand[z['key']] = [_weighted_pick(
                                pool, {n: (om.get(n, 0) + 1) ** 3 for n in pool}, 1, rng)[0]]
                        else:
                            cand[z['key']] = [rng.randint(z['min'], z['max'])]
                t = cand
                if sig_of(cand) not in sigs:
                    break
            sigs.add(sig_of(t))
            out.append(t)
        return out

    main = g['zones'][0]['key']          # 主区（红球/前区/选号）
    for _ in range(count):
        t = None
        for _try in range(40):
            cand = {}
            for z in g['zones']:
                k = need_of(z)
                if strategy == 'anti-crowd' and z['key'] == main:
                    # 全池随机取样，按「与已用号重叠数 → 大众程度」字典序择优，保证同批铺开
                    pool = list(range(z['min'], z['max'] + 1))
                    kk = max(1, min(k, len(pool)))
                    best, bestkey = None, None
                    for _r in range(80):
                        c = sorted(rng.sample(pool, kk))
                        key = (len(set(c) & used), crowd_risk(c, z['max']))
                        if bestkey is None or key < bestkey:
                            best, bestkey = c, key
                        if key[0] == 0:
                            break
                    cand[z['key']] = best
                else:
                    cand[z['key']] = _pick_zone(z, strategy, stats, rng, k)
            t = cand
            if sig_of(cand) not in sigs:
                break
        sigs.add(sig_of(t))
        used.update(t[main])             # 只累积主区，号码池互不干扰
        out.append(t)
    return out


# ───────────────────────── 建议 / 概率 ─────────────────────────

def probability_report(code, play='std'):
    rows = G.prize_table(code)
    return dict(
        game=g['name'] if (g := G.GAMES.get(code)) else code,
        total_win=G.total_win_prob(code, play),
        rows=[dict(play=r['play_name'], level=r['level'], name=r['name'],
                   prob=r['prob'], amount=r['amount']) for r in rows],
    )


def expected_value(code, play='std', sets=1, floats=None):
    """单注期望回报（元）。floats: {层级号: 当期实际奖金}，用于浮动奖估算"""
    price = G.GAMES[code]['price']
    floats = floats or {}
    ev = 0.0
    for _, p, (lv, _nm, amt) in G.hit_distribution(code, play):
        a = amt if amt else floats.get(lv, 0)
        ev += p * (a or 0)
    cost = price * sets
    return dict(cost=cost, ev_single=ev, ev_total=ev * sets,
                roi_est=(ev * sets - cost) / cost if cost else 0,
                has_float=bool(floats))


def advice_text(code, play, strategy, sets, recent_stats=None, floats=None):
    """不承诺收益的诚实建议"""
    price = G.GAMES[code]['price']
    ev = expected_value(code, play, sets, floats)
    tw = G.total_win_prob(code, play)
    top = G.top_prize_prob(code, play)
    tips = []
    if strategy == 'anti-crowd':
        tips.append('反人群只影响「万一中了分多少人」，不改变中奖概率。')
    tips.append('所有策略的中奖概率完全相同，本工具不提供任何优势。')
    if code in ('ssq', 'dlt'):
        tips.append('奖池高会推高销量→撞号变多→同奖级分摊变薄；若要买，冷门号更划算。')
    if not ev.get('has_float'):
        tips.append('未能取到当期浮动奖金额，期望回报按固定奖计算，会低于真实值。')
    return dict(cost=ev['cost'], ev_single=ev['ev_single'], roi_est=ev['roi_est'],
                win_prob=tw, top_prob=top, top_odds=int(1 / top) if top else None,
                has_float=ev.get('has_float', False), tips=tips, price=price)


# ───────────────────────── 浮动奖提取（用于期望值） ─────────────────────────

_CN_LEVEL = {'一等奖': 1, '二等奖': 2, '三等奖': 3, '四等奖': 4, '五等奖': 5,
             '六等奖': 6, '七等奖': 7, '八等奖': 8, '九等奖': 9, '直选': 1}


def floats_from_draw(code, row):
    """从最近一期开奖的奖级表提取 {层级号: 单注金额}"""
    if not row:
        return {}
    try:
        pt = __import__('json').loads(row.get('prize_table') or '{}') or {}
    except Exception:
        return {}
    out = {}
    for k, v in pt.items():
        if not isinstance(v, dict):
            continue
        money = v.get('money')
        if not money:
            continue
        lv = None
        if str(k).isdigit():
            lv = int(k)
        elif str(k) in _CN_LEVEL:
            lv = _CN_LEVEL[str(k)]
        if lv:
            out[lv] = float(money)
    return out
