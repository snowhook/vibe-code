#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app.py — 彩票选号与记账系统（纯标准库，无第三方依赖）"""
import json
import os
import sys
import time
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import analyzer
import fetcher
import games as G
import llm as llm_mod
import store

PORT = int(os.environ.get('PORT', '5010'))
ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, 'static')
COOKIE = 'lt_token'
ORDER = ['ssq', 'dlt', '3d', 'pl3', 'pl5', 'qxc', 'qlc', 'kl8']


# ───────────────────────── 结算 ─────────────────────────

def _prize_from_table(code, d, level, play):
    try:
        pt = json.loads(d.get('prize_table') or '{}') or {}
    except Exception:
        return None
    if code == 'kl8':
        k = int(play) if str(play).isdigit() else 10
        key = 'x%dz%d' % (k, level)
    elif code in ('ssq', 'dlt', 'qlc'):
        key = str(level)
    else:
        key = '直选'
    v = pt.get(key)
    if isinstance(v, dict):
        return v.get('money')
    return None


def _floats(code):
    """从最近一期开奖的奖级表取浮动奖金额，供期望值估算"""
    rows = store.recent_draws(code, 1)
    return analyzer.floats_from_draw(code, rows[0] if rows else None)


# ───────────────────────── AI 按提示词出号 ─────────────────────────

def ai_spec(code, play, sizes=None):
    """给大模型的号码格式说明（按彩种/玩法/每组个数动态生成）

    sizes: {'r': 8, 'b': 5} —— 用户在「每组号码个数」里设的个数。
    大于单注规定个数即为复式，格式说明里必须写实际个数，
    否则模型会一律按标准单注（6 红 1 蓝）给号。
    """
    g = G.GAMES[code]
    if g['type'] == 'digit' and play in ('g3', 'g6'):
        k = 2 if play == 'g3' else 3
        return ('{"tickets":[{"n":[%s]}],"note":"一句话说明"}'
                % ('整数' + '、整数' * (k - 1))
                + '\n"n" 是 %d 个互不相同的整数，取值 0~9。' % k)
    if g['type'] == 'digit':
        keys = ','.join('"%s":[一位数字]' % z['key'] for z in g['zones'])
        desc = '；'.join('%s(%s) 取 %d~%d' % (z['key'], z['name'], z['min'], z['max'])
                        for z in g['zones'])
        return ('{"tickets":[{%s}],"note":"一句话说明"}' % keys
                + '\n每个键是一个长度 1 的数组；%s。' % desc)
    parts, keys = [], []
    for z in g['zones']:
        need = need_count(code, play, z, sizes)
        parts.append('%s(%s) 给 %d 个整数，范围 %d~%d' % (z['key'], z['name'], need, z['min'], z['max']))
        keys.append('"%s":[%d个整数]' % (z['key'], need))
    return ('{"tickets":[{ %s }],"note":"一句话说明"}' % ','.join(keys)
            + '\n' + '；'.join(parts) + '。每个键的号码个数必须完全一致，不要多给也不要少给。')


def need_count(code, play, z, sizes=None):
    """某一区在一注里应当给几个号码（kl8 按玩法档位；组选另算）

    sizes: {'r': 8, 'b': 5} —— 用户设定的「每组号码个数」，有值时优先于
    该区单注规定个数（大于规定个数即为复式）。组选/快乐8 档位不受其影响。
    """
    g = G.GAMES[code]
    if g['type'] == 'digit' and play in ('g3', 'g6'):
        return 2 if play == 'g3' else 3
    if code == 'kl8' and str(play).isdigit():
        return int(play)
    if sizes and z is not None:
        try:
            n = int(sizes.get(z['key']))
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            return n
    return z['count']


def normalize_ticket_ex(code, play, t, strict_count=False, sizes=None):
    """校验并归一化单注号码，返回 (号码, 不合法原因)。

    strict_count=True 时每区个数必须**精确等于**规格 —— 这是 AI 出号用的口径：
    少给会让 bet_count 变成 0（整注被丢，表现为「要 2 注只出了 1 注」），
    多给会被当成复式（1 注膨胀成十几注），两者都不是用户要的「N 注标准注」；
    但用户明确在「每组号码个数」里设了复式个数（sizes）时，按 sizes 判合规。
    手工改号走 strict_count=False，保留复式能力。

    sizes 覆盖「该区应当给几个号」（用户在「每组号码个数」里的设定）。
    """
    if not isinstance(t, dict):
        return None, '号码不是对象'

    def _nums(v, lo, hi, name):
        if v is None:
            return None, '%s 缺失' % name
        if not isinstance(v, (list, tuple)):
            v = [v]
        out = []
        for x in v:
            try:
                n = int(str(x).strip())
            except (TypeError, ValueError):
                return None, '%s 里有非整数（%s）' % (name, x)
            if n < lo or n > hi:
                return None, '%s 的 %s 超出 %d~%d' % (name, n, lo, hi)
            out.append(n)
        if not out:
            return None, '%s 为空' % name
        return sorted(set(out)), None

    g = G.GAMES[code]
    sel = {}
    if g['type'] == 'digit' and play in ('g3', 'g6'):
        need = need_count(code, play, None, sizes)
        v = t.get('n')
        if v is None:
            v = t.get('a')
        ns, why = _nums(v, 0, 9, '组选号码')
        if ns is None:
            return None, why
        if strict_count and len(ns) != need:
            return None, '组选%s需要 %d 个互不相同的数字，收到 %d 个' % (
                '三' if need == 2 else '六', need, len(ns))
        sel['n'] = ns
    else:
        for z in g['zones']:
            need = need_count(code, play, z, sizes)
            ns, why = _nums(t.get(z['key']), z['min'], z['max'], z['name'])
            if ns is None:
                return None, why
            if strict_count and len(ns) != need:
                return None, '%s 需要 %d 个号码，收到 %d 个' % (z['name'], need, len(ns))
            sel[z['key']] = ns
    if G.bet_count(code, play, sel) <= 0:
        return None, '号码组合无法构成有效投注'
    return sel, None


def _mult_of(v, default=1):
    """投注倍数：非数字/越界一律收敛到 1~99，绝不因脏输入报 500。"""
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, 99))


def ticket_text(code, play, t):
    """把一注号码排成人类可读串：红球 03 07 12 19 22 33 | 蓝球 05"""
    g = G.GAMES.get(code)
    if not g:
        return str(t)
    if g['type'] == 'digit' and play in ('g3', 'g6') and t.get('n'):
        return '组选 ' + ' '.join(str(n) for n in t['n'])
    parts = []
    for z in g['zones']:
        v = t.get(z['key'])
        if not v:
            continue
        v = v if isinstance(v, list) else [v]
        try:
            s = ' '.join('%02d' % int(n) for n in v)
        except (TypeError, ValueError):
            s = ' '.join(str(n) for n in v)
        parts.append('%s %s' % (z['name'], s))
    if not parts and t.get('n'):
        parts.append(' '.join(str(n) for n in t['n']))
    return ' | '.join(parts)


def normalize_ticket(code, play, t, strict_count=False, sizes=None):
    """校验并归一化大模型给出的单注号码；不合法返回 None"""
    return normalize_ticket_ex(code, play, t, strict_count, sizes)[0]


def ai_pick_flow(code, play, prompt, sets, sizes=None):
    """AI 按提示词出号（主流程，可单测）。

    最多问两轮：首轮若合法注数不够（模型少给、号码个数不对、号码重复），
    把已给出的号码带上再要一轮并避开它们，尽量凑满用户要求的注数。

    sizes: 用户在「每组号码个数」里的设定（如 {'r': 8, 'b': 5}）。
    有值时按它告诉模型每区给几个号，并按其判合规 —— 大于单注规定个数即为复式，
    否则模型会一律按标准单注（双色球 6 红 1 蓝）出号，无视提示词里的个数要求。
    """
    g = G.GAMES[code]
    play_name = (g['plays'].get(play) or {}).get('name') or play
    spec = ai_spec(code, play, sizes)
    price = g['price']

    tks, per = [], []
    bad, dup, reasons, notes = 0, 0, [], []
    llm_err, raw_txt = None, []
    seen = set()
    for attempt in range(2):
        need = sets - len(tks)
        if need <= 0:
            break
        txt, e = llm_mod.ai_pick(prompt, spec, g['name'], play_name, need,
                                 avoid=(tks if attempt else None))
        if e:
            if not tks:
                llm_err = e              # 一轮都没拿到 → 当致命错误报给前端
            elif len(reasons) < 3:
                reasons.append('补号请求失败（%s）' % e)
            break
        raw_txt.append(txt or '')
        data = _extract_json(txt)
        raw = (data or {}).get('tickets') if isinstance(data, dict) else None
        if not isinstance(raw, list):
            raw = data if isinstance(data, list) else []
        if isinstance(data, dict) and data.get('note'):
            notes.append(str(data['note']))
        for tk in raw:
            if len(tks) >= sets:
                break
            n, why = normalize_ticket_ex(code, play, tk, strict_count=True, sizes=sizes)
            if n is None:
                bad += 1
                if len(reasons) < 3:
                    reasons.append(why)
                continue
            key = json.dumps(n, sort_keys=True, ensure_ascii=False)
            if key in seen:
                dup += 1
                continue
            seen.add(key)
            tks.append(n)
            per.append(G.bet_count(code, play, n))

    tot = sum(per)
    return {'ok': True, 'llm': '\n\n---\n\n'.join(raw_txt) or None,
            'llm_error': llm_err,
            'note': notes[-1] if notes else None,
            'tickets': tks, 'per': per, 'bad': bad, 'dup': dup,
            'bad_reason': reasons, 'got': len(tks), 'sets_wanted': sets,
            'price': price, 'total_sets': tot, 'total_amount': price * tot}


def _extract_json(txt):
    """从模型回复里抠出 JSON（容忍 ```json 代码块与前后废话）"""
    if not txt:
        return None
    t = txt.strip()
    if '```' in t:
        t = t.replace('```json', '```').replace('```JSON', '```')
        seg = t.split('```')
        for s in seg:
            s = s.strip()
            if s.startswith('{') or s.startswith('['):
                t = s
                break
    try:
        return json.loads(t)
    except Exception:
        pass
    i, j = t.find('{'), t.rfind('}')
    if i >= 0 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except Exception:
            return None
    return None



def settle_row(lr):
    """结算该期全部票据 -> (总回款, 命中明细列表, [(ticket_id, 回款, 明细), ...])

    返回 None 表示该期尚无开奖数据。
    """
    code = lr['lottery']
    d = store.get_draw(code, lr['issue'])
    if not d:
        return None
    dr = analyzer.to_draw(code, d)
    tks = store.list_tickets(lr['user_id'], code, lr['issue'])
    zones = G.GAMES[code]['zones']
    total, hits, per = 0.0, [], []
    for t in tks:
        play = t.get('play') or 'std'
        mult = max(1, int(t.get('mult') or 1))
        try:
            combos = G.combos(t['numbers'], zones, play, code)
        except Exception:
            combos = []
        if not combos:
            combos = [t['numbers']]
        tp, th = 0.0, []
        for c in combos[:20000]:
            try:
                pr = G.hit_level(code, play, dr, c)
            except Exception:
                pr = None
            if pr:
                lv, nm, amt = pr
                if amt is None:
                    amt = _prize_from_table(code, d, lv, play) or 0
                # 倍投：单注奖金 × 倍数
                amt = amt * mult
                tp += amt
                th.append('%s +%.0f%s' % (nm, amt, (' ×%d' % mult) if mult > 1 else ''))
        per.append((t['id'], round(tp, 2), '; '.join(th[:8])))
        total += tp
        hits.extend(th)
    return round(total, 2), hits, per


def settle_user(user_id=None):
    out = []
    for lr in store.unsettled(user_id):
        r = settle_row(lr)
        if r is None:
            continue
        total, hits, per = r
        store.mark_tickets_settled(per)
        store.recalc_ledger(lr['user_id'], lr['lottery'], lr['issue'])
        out.append(dict(issue=lr['issue'], lottery=lr['lottery'], ret=total, hits=hits[:12]))
    return out


# ───────────────────────── HTTP ─────────────────────────

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'LotteryApp/1.0'

    def log_message(self, fmt, *args):
        sys.stderr.write('%s [%s] %s\n' % (self.address_string(),
                                           time.strftime('%H:%M:%S'), fmt % args))

    # -------- helpers --------
    def _body(self):
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            return {}

    def _send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _json(self, obj, code=200, extra=None):
        self._send(code, obj, extra=extra)

    def _user(self):
        ck = SimpleCookie(self.headers.get('Cookie') or '')
        tok = ck[COOKIE].value if COOKIE in ck else None
        return store.session_user(tok), tok

    def _client_ip(self):
        return (self.headers.get('X-Real-IP')
                or self.headers.get('X-Forwarded-For', '').split(',')[0].strip()
                or self.client_address[0])

    def _need(self):
        u, _ = self._user()
        if not u:
            self._json({'ok': False, 'error': '未登录', 'need_login': True}, 401)
        return u

    def _need_admin(self):
        u = self._need()
        if u and u.get('role') != 'admin':
            self._json({'ok': False, 'error': '需要管理员权限'}, 403)
            return None
        return u

    # -------- GET --------
    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(p.query)
        path = p.path
        if path in ('/', '/index.html'):
            return self._static('index.html')
        if path.startswith('/static/'):
            return self._static(path[len('/static/'):])
        try:
            return self._api_get(path, q)
        except Exception as e:
            return self._json({'ok': False, 'error': '%s: %s' % (type(e).__name__, e)}, 500)

    def _api_get(self, path, q):
        one = lambda k, d=None: (q.get(k) or [d])[0]
        if path == '/api/games':
            out = []
            for c in ORDER:
                g = G.GAMES[c]
                out.append(dict(code=c, name=g['name'], org=g['org'], type=g['type'],
                                price=g['price'], note=g['note'],
                                zones=[dict(z) for z in g['zones']],
                                plays=[dict(v) for v in g['plays'].values()]))
            return self._json({'ok': True, 'games': out,
                               'strategies': [dict(key=k, **v) for k, v in analyzer.STRATEGIES.items()]})
        if path == '/api/me':
            u, _ = self._user()
            return self._json({'ok': True, 'user': u})
        if path == '/api/draws':
            lt = one('lottery', 'ssq')
            lim = int(one('limit', '30'))
            rows = store.recent_draws(lt, lim)
            return self._json({'ok': True, 'lottery': lt, 'draws': rows})
        if path == '/api/stats':
            lt = one('lottery', 'ssq')
            draws = store.all_draws(lt)
            st = analyzer.Stats(lt, draws)
            zk = one('zone')
            if not zk:
                zk = G.GAMES[lt]['zones'][0]['key']
            z = next(zz for zz in G.GAMES[lt]['zones'] if zz['key'] == zk)
            fr = st.freq(zk)
            om = st.omission(zk)
            pool = list(range(z['min'], z['max'] + 1))
            return self._json({'ok': True, 'lottery': lt, 'zone': zk, 'zone_name': z['name'],
                               'total': st.n,
                               'items': [dict(num=n, count=fr.get(n, 0), omission=om.get(n))
                                         for n in pool]})
        if path == '/api/probability':
            lt, play = one('lottery', 'ssq'), one('play', 'std')
            return self._json({'ok': True, 'report': analyzer.probability_report(lt, play),
                               'ev': analyzer.expected_value(lt, play, 1, _floats(lt))})
        if path == '/api/tickets':
            u = self._need()
            if not u:
                return
            return self._json({'ok': True, 'tickets': store.list_tickets(
                u['id'], one('lottery'), one('issue'))})
        if path == '/api/ledger':
            u = self._need()
            if not u:
                return
            lt = one('lottery')
            return self._json({'ok': True, 'rows': store.get_ledger(u['id'], lt, 300,
                               one('sort') or 'date'),
                               'summary': store.ledger_summary(u['id'], lt)})
        if path == '/api/options':
            u = self._need()
            if not u:
                return
            cfg = llm_mod.get_config()
            return self._json({'ok': True, 'presets': llm_mod.PRESETS,
                               'llm': llm_mod.mask(cfg),
                               'users': store.list_users() if u.get('role') == 'admin' else None,
                               'db': store.stats()})
        return self._json({'ok': False, 'error': '未知接口'}, 404)

    def _static(self, name):
        fp = os.path.join(STATIC, os.path.basename(name))
        if not os.path.exists(fp):
            return self._json({'ok': False, 'error': 'not found'}, 404)
        ctype = 'text/html; charset=utf-8' if fp.endswith('.html') else 'application/octet-stream'
        with open(fp, 'rb') as f:
            data = f.read()
        return self._send(200, data, ctype)

    # -------- POST --------
    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        try:
            return self._api_post(p.path)
        except Exception as e:
            return self._json({'ok': False, 'error': '%s: %s' % (type(e).__name__, e)}, 500)

    def _api_post(self, path):
        b = self._body()
        if path == '/api/login':
            ip = self._client_ip()
            if store.fail_count(ip) >= 8:
                return self._json({'ok': False, 'error': '尝试过于频繁，请 15 分钟后再试'}, 429)
            u = store.verify_user(b.get('username'), b.get('password'))
            if not u:
                store.record_fail(ip)
                return self._json({'ok': False, 'error': '用户名或密码错误'}, 401)
            store.clear_fails(ip)
            tok = store.new_session(u['id'])
            return self._json({'ok': True, 'user': u},
                              extra={'Set-Cookie': '%s=%s; Path=/; HttpOnly; Max-Age=%d; SameSite=Lax'
                                                   % (COOKIE, tok, 30 * 86400)})
        if path == '/api/logout':
            _, tok = self._user()
            if tok:
                store.drop_session(tok)
            return self._json({'ok': True},
                              extra={'Set-Cookie': '%s=; Path=/; Max-Age=0' % COOKIE})
        u = self._need()
        if not u:
            return

        if path == '/api/generate':
            code = b.get('lottery') or 'ssq'
            play = b.get('play') or 'std'
            strategy = b.get('strategy') or 'random'
            sets = max(1, min(int(b.get('sets') or 1), 500))
            picks = b.get('picks') or {}
            sizes = b.get('sizes') or {}
            if not isinstance(sizes, dict):
                sizes = {}
            stats = None
            if strategy != 'random':
                stats = analyzer.Stats(code, store.all_draws(code))
            if picks and any(picks.get(z['key']) for z in G.GAMES[code]['zones']):
                nums = [dict(picks)]
            else:
                nums = analyzer.generate(code, play, strategy, sets,
                                         seed=b.get('seed'), stats=stats, sizes=sizes)
            cnt = sum(G.bet_count(code, play, n) or 0 for n in nums) or len(nums)
            fl = _floats(code)
            ev = analyzer.expected_value(code, play, cnt, fl)
            adv = analyzer.advice_text(code, play, strategy, cnt, floats=fl)
            return self._json({'ok': True, 'tickets': nums, 'bet_count': cnt, 'groups': len(nums),
                               'cost': ev['cost'], 'ev': ev['ev_total'],
                               'roi_est': ev['roi_est'], 'advice': adv})

        if path == '/api/probability':
            code, play = b.get('lottery') or 'ssq', b.get('play') or 'std'
            picks = b.get('picks') or {}
            cnt = G.bet_count(code, play, picks) if picks else 0
            rep = analyzer.probability_report(code, play)
            ev = analyzer.expected_value(code, play, max(cnt, 1), _floats(code))
            return self._json({'ok': True, 'report': rep, 'bet_count': cnt,
                               'cost': G.GAMES[code]['price'] * cnt,
                               'ev': ev['ev_total'], 'roi_est': ev['roi_est']})

        if path == '/api/bet':
            code = b.get('lottery') or 'ssq'
            play = b.get('play') or 'std'
            issue = b.get('issue') or (store.latest_issue(code) or '')
            numbers = b.get('numbers') or {}
            mult = _mult_of(b.get('mult'))
            base = G.bet_count(code, play, numbers) or int(b.get('sets') or 1)
            sets = base * mult
            amount = G.GAMES[code]['price'] * sets
            store.add_ticket(u['id'], code, play, issue, numbers, sets, amount,
                             b.get('advice') or '', b.get('acted') or '', b.get('signal') or '',
                             mult=mult)
            store.recalc_ledger(u['id'], code, issue, meta={
                'advice': b.get('advice') or '', 'acted': b.get('acted') or '',
                'signal': b.get('signal') or '', 'note': b.get('note') or ''})
            return self._json({'ok': True, 'sets': sets, 'amount': amount, 'mult': mult})

        if path == '/api/ticket/delete':
            tid = int(b.get('id') or 0)
            tk = store.get_ticket(u['id'], tid)
            n = store.delete_ticket(u['id'], tid)
            if tk:
                store.recalc_ledger(u['id'], tk['lottery'], tk['issue'])
            return self._json({'ok': True, 'deleted': n})

        if path == '/api/update':
            lt = b.get('lottery') or 'all'
            codes = ORDER if lt == 'all' else [lt]
            src = b.get('source') or 'auto'
            res = []
            for c in codes:
                try:
                    rows, tag = fetcher.fetch(c, store.latest_issue(c),
                                              int(b.get('pages') or 4), source=src)
                    # 镜像源不含销售额/奖池/奖级表，只补新期，避免覆盖官方富数据
                    n = store.upsert_draws(rows, only_new=(tag == 'mirror'))
                    store.log_update(c, n)
                    res.append(dict(lottery=c, inserted=n, source=tag, ok=True,
                                    latest=store.latest_issue(c)))
                except Exception as e:
                    res.append(dict(lottery=c, ok=False,
                                    error='%s: %s' % (type(e).__name__, e)))
            settled = settle_user(u['id'])
            return self._json({'ok': True, 'result': res, 'settled': settled})

        if path == '/api/settle':
            return self._json({'ok': True, 'settled': settle_user(u['id'])})

        if path == '/api/advice':
            code = b.get('lottery') or 'ssq'
            play = b.get('play') or 'std'
            tickets = b.get('tickets') or []
            mults = b.get('mults') if isinstance(b.get('mults'), list) else []
            if tickets:
                # 点评「自选池」：注数/金额按池内实际号码与倍数算
                cnt = 0
                for i, t in enumerate(tickets):
                    try:
                        base = G.bet_count(code, play, t) or 0
                    except Exception:
                        base = 0
                    m = _mult_of(mults[i]) if i < len(mults) else 1
                    cnt += base * m
            else:
                cnt = int(b.get('sets') or 1)
            ctx = analyzer.advice_text(code, play, b.get('strategy') or 'random', max(cnt, 1),
                                       floats=_floats(code))
            ctx.update(dict(lottery=G.GAMES[code]['name'],
                            play=(G.GAMES[code]['plays'].get(play) or {}).get('name') or play,
                            recent=store.recent_draws(code, 5)))
            if tickets:
                # 明确区分「组」和「注」：一组可能是复式（展开成多注），
                # 只给一个数字会让模型把 2 组复式说成「28 注号码」。
                ctx['自选池组数'] = len(tickets)
                ctx['自选池注数_展开后'] = cnt
                ctx['自选池号码_每组一组'] = [ticket_text(code, play, t) for t in tickets]
            if not b.get('use_llm'):
                return self._json({'ok': True, 'advice': ctx, 'llm': None})
            payload = {k: v for k, v in ctx.items() if k != 'recent'}
            payload['最近5期'] = [r['issue'] for r in store.recent_draws(code, 5)]
            if tickets:
                txt, err = llm_mod.pool_review(payload)
            else:
                txt, err = llm_mod.lottery_advice(payload)
            return self._json({'ok': True, 'advice': ctx, 'llm': txt, 'llm_error': err})

        if path == '/api/ai/pick':
            code = b.get('lottery') or 'ssq'
            play = b.get('play') or 'std'
            prompt = (b.get('prompt') or '').strip()
            sets = max(1, min(int(b.get('sets') or 5), 20))
            sizes = b.get('sizes') or {}
            if not isinstance(sizes, dict):
                sizes = {}
            if not prompt:
                return self._json({'ok': False, 'error': '请先填写提示词，告诉 AI 你的选号要求'})
            return self._json(ai_pick_flow(code, play, prompt, sets, sizes))

        if path == '/api/pool/calc':
            code = b.get('lottery') or 'ssq'
            play = b.get('play') or 'std'
            tks = b.get('tickets') or []
            # 每注可带自己的投注倍数；缺省 1。mults 与 tickets 一一对应。
            mults = b.get('mults')
            if not isinstance(mults, list):
                mults = []
            per, per_mult = [], []
            for i, t in enumerate(tks):
                try:
                    per.append(G.bet_count(code, play, t) or 0)
                except Exception:
                    per.append(0)
                try:
                    m = int(mults[i]) if i < len(mults) else 1
                except (TypeError, ValueError):
                    m = 1
                per_mult.append(max(1, min(m, 99)))
            price = G.GAMES[code]['price']
            total_sets = sum(n * m for n, m in zip(per, per_mult))
            return self._json({'ok': True, 'per': per, 'mults': per_mult, 'price': price,
                               'per_amount': [n * m * price for n, m in zip(per, per_mult)],
                               'total_sets': total_sets, 'total_amount': price * total_sets})

        if path == '/api/ticket/update':
            tid = int(b.get('id') or 0)
            tk = store.get_ticket(u['id'], tid)
            if not tk:
                return self._json({'ok': False, 'error': '这条投注记录不存在'})
            code = tk['lottery']
            play = b.get('play') or tk['play'] or 'std'
            issue = str(b.get('issue') or tk['issue']).strip() or tk['issue']
            numbers = b.get('numbers') or {}
            n2 = normalize_ticket(code, play, numbers)
            if n2 is None:
                return self._json({'ok': False, 'error': '号码不符合该彩种玩法的要求，请检查'})
            mult = _mult_of(b.get('mult'), default=(tk.get('mult') or 1))
            sets = G.bet_count(code, play, n2) * mult
            amount = G.GAMES[code]['price'] * sets
            store.update_ticket(u['id'], tid, play, issue, n2, sets, amount,
                                b.get('advice') if 'advice' in b else None, mult=mult)
            old_issue = tk['issue']
            store.recalc_ledger(u['id'], code, old_issue)
            if issue != old_issue:
                store.recalc_ledger(u['id'], code, issue)
            settled = settle_user(u['id'])
            return self._json({'ok': True, 'sets': sets, 'amount': amount, 'mult': mult,
                               'issue': issue, 'settled': settled})

        if path == '/api/ledger/delete':
            code = b.get('lottery') or ''
            issue = str(b.get('issue') or '').strip()
            if not code or not issue:
                return self._json({'ok': False, 'error': '缺少彩种或期号'})
            n = store.delete_round(u['id'], code, issue)
            return self._json({'ok': True, 'deleted': n})

        if path == '/api/settings/llm':
            if u.get('role') != 'admin':
                return self._json({'ok': False, 'error': '需要管理员权限'}, 403)
            cfg = llm_mod.save_config(b.get('config') or {})
            return self._json({'ok': True, 'llm': llm_mod.mask(cfg)})

        if path == '/api/password':
            # 任何登录用户都可改自己的密码（需验原密码）
            old = b.get('old') or ''
            new = b.get('new') or ''
            if len(new) < 6:
                return self._json({'ok': False, 'error': '新密码至少 6 位'})
            if new == old:
                return self._json({'ok': False, 'error': '新密码与原密码相同'})
            if not store.verify_user(u['username'], old):
                return self._json({'ok': False, 'error': '原密码不正确'})
            store.set_password(u['id'], new)
            return self._json({'ok': True})

        if path == '/api/users':
            if u.get('role') != 'admin':
                return self._json({'ok': False, 'error': '需要管理员权限'}, 403)
            act = b.get('action')
            if act == 'add':
                uid, err = store.create_user(b.get('username'), b.get('password'), b.get('role') or 'user')
                return self._json({'ok': not err, 'error': err, 'id': uid})
            if act == 'passwd':
                store.set_password(int(b.get('id')), b.get('password') or '')
                return self._json({'ok': True})
            if act == 'delete':
                if int(b.get('id')) == u['id']:
                    return self._json({'ok': False, 'error': '不能删除自己'})
                store.delete_user(int(b.get('id')))
                return self._json({'ok': True})
            return self._json({'ok': False, 'error': '未知操作'})

        return self._json({'ok': False, 'error': '未知接口'}, 404)


def bootstrap():
    store.init_db()
    c = store.conn()
    n = c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    c.close()
    if n == 0:
        import secrets
        pw = os.environ.get('ADMIN_PASS') or ('lt' + secrets.token_hex(4))
        store.create_user('admin', pw, 'admin')
        print('=' * 52)
        print(' 已创建管理员账号 -> 用户名: admin   密码: %s' % pw)
        print(' 请立即登录并修改密码。')
        print('=' * 52)


def main():
    bootstrap()
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print('lottery app listening on 127.0.0.1:%d' % PORT, flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
