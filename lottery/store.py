# -*- coding: utf-8 -*-
"""store.py — SQLite 数据层：开奖数据 / 用户 / 会话 / 投注 / 账本 / 配置"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time

DB_PATH = os.environ.get('LOTTERY_DB') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'data', 'lottery.db')

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS draws (
  lottery TEXT NOT NULL, issue TEXT NOT NULL, draw_date TEXT,
  zone1 TEXT, zone2 TEXT,
  sales REAL DEFAULT 0, pool REAL DEFAULT 0,
  first_prize_count INTEGER DEFAULT 0, first_prize_amount REAL DEFAULT 0,
  prize_table TEXT DEFAULT '', fetched_at TEXT,
  PRIMARY KEY (lottery, issue)
);
CREATE INDEX IF NOT EXISTS idx_draws_lottery_issue ON draws(lottery, issue);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  pass_hash TEXT NOT NULL, salt TEXT NOT NULL,
  role TEXT DEFAULT 'user', created_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL, lottery TEXT NOT NULL, play TEXT DEFAULT 'std',
  issue TEXT NOT NULL, numbers_json TEXT NOT NULL,
  sets INTEGER DEFAULT 1, amount REAL DEFAULT 0, mult INTEGER DEFAULT 1,
  advice TEXT DEFAULT '', acted TEXT DEFAULT '', signal TEXT DEFAULT '',
  created_at TEXT, settled INTEGER DEFAULT 0,
  prize_amount REAL DEFAULT 0, hit_detail TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL, lottery TEXT NOT NULL, issue TEXT NOT NULL,
  advice TEXT DEFAULT '', acted TEXT DEFAULT '',
  sets INTEGER DEFAULT 0, cost REAL DEFAULT 0, return_amount REAL DEFAULT 0,
  settled INTEGER DEFAULT 0, signal TEXT DEFAULT '', note TEXT DEFAULT '',
  created_at TEXT,
  UNIQUE(user_id, lottery, issue)
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY, value TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS user_settings (
  user_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT,
  PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS login_fails (
  ip TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS update_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lottery TEXT, inserted INTEGER, note TEXT, ts TEXT
);
"""


def _ensure_dir():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def conn():
    _ensure_dir()
    c = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=20000')
    return c


def init_db():
    c = conn()
    c.executescript(SCHEMA)
    c.commit()
    _migrate(c)
    c.close()


def _migrate(c):
    """轻量迁移：老库缺列时补上（CREATE TABLE IF NOT EXISTS 不会改已存在的表）。

    mult = 投注倍数。注意 sets 的语义是「含倍数后的总注数」，
    amount = 单价 × sets，账本「注数×单价=投入」的口径因此始终成立。
    """
    try:
        cols = [r[1] for r in c.execute('PRAGMA table_info(tickets)')]
        if 'mult' not in cols:
            c.execute('ALTER TABLE tickets ADD COLUMN mult INTEGER DEFAULT 1')
        c.execute('UPDATE tickets SET mult=1 WHERE mult IS NULL OR mult<1')
        c.commit()
    except Exception:
        c.rollback()


def now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


# ───────────────────────── 用户 / 认证 ─────────────────────────

def _hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                               bytes.fromhex(salt), 120000).hex()


def create_user(username, password, role='user'):
    username = (username or '').strip()
    if not username or len(password or '') < 6:
        return None, '用户名不能为空，密码至少 6 位'
    salt = secrets.token_hex(16)
    c = conn()
    try:
        c.execute('INSERT INTO users (username, pass_hash, salt, role, created_at) VALUES (?,?,?,?,?)',
                  (username, _hash(password, salt), salt, role, now()))
        c.commit()
        uid = c.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()[0]
        return uid, None
    except sqlite3.IntegrityError:
        return None, '用户名已存在'
    finally:
        c.close()


def verify_user(username, password):
    c = conn()
    try:
        r = c.execute('SELECT id, pass_hash, salt, role FROM users WHERE username=?',
                      ((username or '').strip(),)).fetchone()
        if not r:
            return None
        if hmac.compare_digest(r['pass_hash'], _hash(password or '', r['salt'])):
            return dict(id=r['id'], username=username, role=r['role'])
        return None
    finally:
        c.close()


def new_session(user_id, days=30):
    tok = secrets.token_urlsafe(32)
    c = conn()
    c.execute('INSERT INTO sessions (token, user_id, expires_at) VALUES (?,?,?)',
              (tok, user_id, time.time() + days * 86400))
    c.commit()
    c.close()
    return tok


def session_user(token):
    if not token:
        return None
    c = conn()
    try:
        r = c.execute('SELECT s.user_id, s.expires_at, u.username, u.role '
                      'FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?',
                      (token,)).fetchone()
        if not r or r['expires_at'] < time.time():
            return None
        return dict(id=r['user_id'], username=r['username'], role=r['role'])
    finally:
        c.close()


def drop_session(token):
    c = conn()
    c.execute('DELETE FROM sessions WHERE token=?', (token,))
    c.commit()
    c.close()


def list_users():
    c = conn()
    try:
        return [dict(r) for r in c.execute(
            'SELECT id, username, role, created_at FROM users ORDER BY id')]
    finally:
        c.close()


def set_password(uid, password):
    salt = secrets.token_hex(16)
    c = conn()
    c.execute('UPDATE users SET pass_hash=?, salt=? WHERE id=?', (_hash(password, salt), salt, uid))
    c.commit()
    c.close()


def delete_user(uid):
    c = conn()
    for t in ('sessions', 'tickets', 'ledger'):
        c.execute('DELETE FROM %s WHERE user_id=?' % t, (uid,))
    c.execute('DELETE FROM user_settings WHERE user_id=?', (uid,))
    c.execute('DELETE FROM users WHERE id=?', (uid,))
    c.commit()
    c.close()


def record_fail(ip):
    c = conn()
    c.execute('INSERT INTO login_fails (ip, ts) VALUES (?,?)', (ip, time.time()))
    c.commit()
    c.close()


def fail_count(ip, window=900):
    c = conn()
    try:
        return c.execute('SELECT COUNT(*) FROM login_fails WHERE ip=? AND ts>?',
                         (ip, time.time() - window)).fetchone()[0]
    finally:
        c.close()


def clear_fails(ip):
    c = conn()
    c.execute('DELETE FROM login_fails WHERE ip=?', (ip,))
    c.commit()
    c.close()


# ───────────────────────── 开奖数据 ─────────────────────────

def upsert_draws(rows, only_new=False):
    """写入开奖数据。

    only_new=True 时仅插入不存在的期次（INSERT OR IGNORE），
    用于镜像源补号：镜像无销售额/奖池/奖级表，若走覆盖写会把
    官方源已有的富数据抹成 0。
    """
    if not rows:
        return 0
    c = conn()
    if only_new:
        cur = c.executemany(
            'INSERT OR IGNORE INTO draws (lottery, issue, draw_date, zone1, zone2, sales, pool, '
            'first_prize_count, first_prize_amount, prize_table, fetched_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?)', rows)
        n = cur.rowcount
    else:
        c.executemany(
            'INSERT INTO draws (lottery, issue, draw_date, zone1, zone2, sales, pool, '
            'first_prize_count, first_prize_amount, prize_table, fetched_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?) '
            'ON CONFLICT(lottery, issue) DO UPDATE SET '
            'draw_date=excluded.draw_date, zone1=excluded.zone1, zone2=excluded.zone2, '
            'sales=excluded.sales, pool=excluded.pool, '
            'first_prize_count=excluded.first_prize_count, first_prize_amount=excluded.first_prize_amount, '
            'prize_table=excluded.prize_table, fetched_at=excluded.fetched_at', rows)
        n = len(rows)
    c.commit()
    c.close()
    return n


def latest_issue(lottery):
    c = conn()
    try:
        r = c.execute('SELECT issue FROM draws WHERE lottery=? ORDER BY issue DESC LIMIT 1',
                      (lottery,)).fetchone()
        return r[0] if r else None
    finally:
        c.close()


def next_issue(lottery):
    """下注应针对的「下一期」= 最新已开奖期号 + 1。

    修复历史 bug：/api/bet 默认用 latest_issue（最新**已开奖**期）打标，
    导致用户在下一期开奖前下注会被贴成上一期的号、再拿上一期奖号结算。
    期号格式为「年份前缀 + 3 位序号」（ssq 2026xxx / dlt 26xxx / pl3 26xxx /
    kl8 2026xxx 等），年内整数 +1 即下一期；年份交界属罕见边角，可手动改期号兜底。
    """
    cur = latest_issue(lottery)
    if not cur:
        return None
    try:
        return str(int(cur) + 1)
    except ValueError:
        return None



def get_draw(lottery, issue):
    c = conn()
    try:
        r = c.execute('SELECT * FROM draws WHERE lottery=? AND issue=?', (lottery, issue)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def recent_draws(lottery, limit=50):
    c = conn()
    try:
        return [dict(r) for r in c.execute(
            'SELECT * FROM draws WHERE lottery=? ORDER BY issue DESC LIMIT ?', (lottery, limit))]
    finally:
        c.close()


def draws_since(lottery, since_issue=None, limit=400):
    """按时间升序返回，用于统计（可限定起始期号）"""
    c = conn()
    try:
        if since_issue:
            rows = c.execute('SELECT * FROM draws WHERE lottery=? AND issue>=? ORDER BY issue ASC',
                             (lottery, since_issue)).fetchall()
        else:
            rows = c.execute('SELECT * FROM draws WHERE lottery=? ORDER BY issue DESC LIMIT ?',
                             (lottery, limit)).fetchall()
            rows = rows[::-1]
        return [dict(r) for r in rows]
    finally:
        c.close()


def all_draws(lottery):
    c = conn()
    try:
        return [dict(r) for r in c.execute(
            'SELECT * FROM draws WHERE lottery=? ORDER BY issue ASC', (lottery,))]
    finally:
        c.close()


def log_update(lottery, inserted, note=''):
    c = conn()
    c.execute('INSERT INTO update_log (lottery, inserted, note, ts) VALUES (?,?,?,?)',
              (lottery, inserted, note, now()))
    c.commit()
    c.close()


# ───────────────────────── 投注 / 账本 ─────────────────────────

def add_ticket(user_id, lottery, play, issue, numbers, sets, amount,
               advice='', acted='', signal='', mult=1):
    c = conn()
    c.execute('INSERT INTO tickets (user_id, lottery, play, issue, numbers_json, sets, amount, mult, '
              'advice, acted, signal, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
              (user_id, lottery, play, issue, json.dumps(numbers, ensure_ascii=False),
               sets, amount, max(1, int(mult or 1)), advice, acted, signal, now()))
    c.commit()
    tid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.close()
    return tid


def list_tickets(user_id, lottery=None, issue=None, limit=200):
    c = conn()
    try:
        sql = 'SELECT * FROM tickets WHERE user_id=?'
        args = [user_id]
        if lottery:
            sql += ' AND lottery=?'
            args.append(lottery)
        if issue:
            sql += ' AND issue=?'
            args.append(issue)
        sql += ' ORDER BY id DESC LIMIT ?'
        args.append(limit)
        out = []
        for r in c.execute(sql, args):
            d = dict(r)
            try:
                d['numbers'] = json.loads(d.pop('numbers_json') or '{}')
            except Exception:
                d['numbers'] = {}
            out.append(d)
        return out
    finally:
        c.close()


def get_ticket(user_id, tid):
    c = conn()
    try:
        r = c.execute('SELECT * FROM tickets WHERE id=? AND user_id=?', (tid, user_id)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def delete_ticket(user_id, tid):
    c = conn()
    try:
        c.execute('DELETE FROM tickets WHERE id=? AND user_id=?', (tid, user_id))
        c.commit()
        return c.total_changes
    finally:
        c.close()


def update_ticket(user_id, tid, play, issue, numbers, sets, amount, advice=None, mult=1):
    """修改一注投注记录。号码/期号/玩法/倍数变了，结算状态必须重置，等下次对奖重算。"""
    c = conn()
    try:
        nj = json.dumps(numbers, ensure_ascii=False)
        mult = max(1, int(mult or 1))
        if advice is None:
            c.execute('UPDATE tickets SET play=?, issue=?, numbers_json=?, sets=?, amount=?, mult=?, '
                      'settled=0, prize_amount=0, hit_detail=? WHERE id=? AND user_id=?',
                      (play, issue, nj, sets, amount, mult, '', tid, user_id))
        else:
            c.execute('UPDATE tickets SET play=?, issue=?, numbers_json=?, sets=?, amount=?, mult=?, '
                      'advice=?, settled=0, prize_amount=0, hit_detail=? WHERE id=? AND user_id=?',
                      (play, issue, nj, sets, amount, mult, advice, '', tid, user_id))
        c.commit()
        return c.total_changes
    finally:
        c.close()


def delete_round(user_id, lottery, issue):
    """删除某一期的全部投注记录（连带账本行）。返回删除的票据数。"""
    c = conn()
    try:
        c.execute('DELETE FROM tickets WHERE user_id=? AND lottery=? AND issue=?',
                  (user_id, lottery, issue))
        n = c.total_changes
        c.execute('DELETE FROM ledger WHERE user_id=? AND lottery=? AND issue=?',
                  (user_id, lottery, issue))
        c.commit()
        return n
    finally:
        c.close()


def recalc_ledger(user_id, lottery, issue, meta=None):
    """按 tickets 表重算该期账本聚合。

    账本口径 = 该期所有票据的集合：
      sets/cost 累加、return 累加、settled 取「全部已结算」。
    票据删空则账本行一并移除，避免孤儿记录。
    """
    c = conn()
    try:
        row = c.execute(
            'SELECT COUNT(*) AS n, COALESCE(SUM(sets),0) AS s, '
            'COALESCE(SUM(amount),0) AS amt, COALESCE(SUM(prize_amount),0) AS pr, '
            'COALESCE(MIN(settled),1) AS st '
            'FROM tickets WHERE user_id=? AND lottery=? AND issue=?',
            (user_id, lottery, issue)).fetchone()
        if not row or not row['n']:
            c.execute('DELETE FROM ledger WHERE user_id=? AND lottery=? AND issue=?',
                      (user_id, lottery, issue))
            c.commit()
            return None
        sets, cost = int(row['s'] or 0), float(row['amt'] or 0)
        ret, settled = float(row['pr'] or 0), (1 if row['st'] else 0)
        m = meta or {}
        cur = c.execute('SELECT id FROM ledger WHERE user_id=? AND lottery=? AND issue=?',
                        (user_id, lottery, issue)).fetchone()
        if cur:
            if meta:
                c.execute('UPDATE ledger SET advice=?, acted=?, signal=?, note=?, sets=?, cost=?, '
                          'return_amount=?, settled=? WHERE id=?',
                          (m.get('advice', ''), m.get('acted', ''), m.get('signal', ''),
                           m.get('note', ''), sets, cost, ret, settled, cur['id']))
            else:
                c.execute('UPDATE ledger SET sets=?, cost=?, return_amount=?, settled=? WHERE id=?',
                          (sets, cost, ret, settled, cur['id']))
            lid = cur['id']
        else:
            c.execute('INSERT INTO ledger (user_id, lottery, issue, advice, acted, sets, cost, '
                      'signal, note, return_amount, settled, created_at) '
                      'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                      (user_id, lottery, issue, m.get('advice', ''), m.get('acted', ''),
                       sets, cost, m.get('signal', ''), m.get('note', ''), ret, settled, now()))
            lid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.commit()
        return lid
    finally:
        c.close()


def mark_tickets_settled(rows):
    """rows: [(ticket_id, prize_amount, hit_detail), ...]"""
    if not rows:
        return 0
    c = conn()
    try:
        for tid, prize, detail in rows:
            c.execute('UPDATE tickets SET settled=1, prize_amount=?, hit_detail=? WHERE id=?',
                      (prize, detail or '', tid))
        c.commit()
        return len(rows)
    finally:
        c.close()


def upsert_ledger(user_id, lottery, issue, advice, acted, sets, cost,
                  signal='', note='', return_amount=None, settled=None):
    c = conn()
    cur = c.execute('SELECT id, return_amount, settled FROM ledger WHERE user_id=? AND lottery=? AND issue=?',
                    (user_id, lottery, issue)).fetchone()
    if cur:
        c.execute('UPDATE ledger SET advice=?, acted=?, sets=?, cost=?, signal=?, note=?, '
                  'return_amount=COALESCE(?, return_amount), settled=COALESCE(?, settled) '
                  'WHERE id=?',
                  (advice, acted, sets, cost, signal, note,
                   return_amount, settled, cur['id']))
        lid = cur['id']
    else:
        c.execute('INSERT INTO ledger (user_id, lottery, issue, advice, acted, sets, cost, '
                  'signal, note, return_amount, settled, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                  (user_id, lottery, issue, advice, acted, sets, cost, signal, note,
                   return_amount or 0, settled or 0, now()))
        lid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.commit()
    c.close()
    return lid


def get_ledger(user_id, lottery=None, limit=200, sort='date'):
    """账本明细（含投注日期）。

    投注日期取该期 tickets 的 MIN(created_at) —— ledger.created_at 是账本行落库时刻，
    补记/重算时会变；tickets.created_at 才是真正按下注的时刻。tickets 被清空时退回
    ledger.created_at 兜底，避免老数据出现空白日期。

    sort='date'（默认，按下注日期倒序）/ 'issue'（按期号倒序）/ 'cost'（按投入倒序）。
    跨彩种时期号之间不可比（各彩种期号编码规则不同），所以日期是唯一稳妥的默认序。
    """
    c = conn()
    try:
        sql = ('SELECT l.*, MIN(t.created_at) AS bet_at, COUNT(t.id) AS ticket_cnt '
               'FROM ledger l LEFT JOIN tickets t '
               '  ON t.user_id=l.user_id AND t.lottery=l.lottery AND t.issue=l.issue '
               'WHERE l.user_id=?')
        args = [user_id]
        if lottery:
            sql += ' AND l.lottery=?'
            args.append(lottery)
        sql += ' GROUP BY l.id'
        order = {'issue': 'l.issue DESC, l.id DESC',
                 'cost': 'l.cost DESC, l.id DESC',
                 'settled': 'l.settled ASC, l.id DESC'}.get(sort)
        if not order:
            # COALESCE 兜底：无票据的老行也要能按日期落位，不能整堆排到末尾
            order = "COALESCE(MIN(t.created_at), l.created_at) DESC, l.id DESC"
        sql += ' ORDER BY ' + order + ' LIMIT ?'
        args.append(limit)
        out = []
        for r in c.execute(sql, args):
            d = dict(r)
            d['bet_at'] = d.get('bet_at') or d.get('created_at') or ''
            d['ticket_cnt'] = d.get('ticket_cnt') or 0
            out.append(d)
        return out
    finally:
        c.close()


def ledger_summary(user_id, lottery=None):
    c = conn()
    try:
        sql = ('SELECT COUNT(*) n, COALESCE(SUM(sets),0) sets, COALESCE(SUM(cost),0) cost, '
               'COALESCE(SUM(return_amount),0) ret, COALESCE(SUM(settled),0) settled, '
               'COALESCE(SUM(CASE WHEN return_amount>0 THEN 1 ELSE 0 END),0) win_rounds '
               'FROM ledger WHERE user_id=?')
        args = [user_id]
        if lottery:
            sql += ' AND lottery=?'
            args.append(lottery)
        r = c.execute(sql, args).fetchone()
        cost = r['cost'] or 0
        ret = r['ret'] or 0
        # 中奖注数：tickets 表 prize_amount>0 的注数（与 lottery 过滤对齐）
        wt_sql = 'SELECT COUNT(*) FROM tickets WHERE user_id=? AND prize_amount>0'
        wt_args = [user_id]
        if lottery:
            wt_sql += ' AND lottery=?'
            wt_args.append(lottery)
        win_tickets = c.execute(wt_sql, wt_args).fetchone()[0]
        return dict(rounds=r['n'], sets=r['sets'], cost=cost, ret=ret,
                    net=ret - cost, roi=(ret - cost) / cost if cost else 0,
                    settled_rounds=r['settled'], win_rounds=r['win_rounds'],
                    win_tickets=win_tickets)
    finally:
        c.close()


def ledger_trend(user_id, lottery=None, by='day'):
    """账本趋势：按投注日期（日/月）聚合投入与中奖回款，供前端画图。

    bet_at 来自 tickets 首次下注时间，无票据行退回 ledger.created_at（与 get_ledger 一致）。
    返回升序列表 [{date, cost, ret, net}]，date 形如 2026-10-15（日）或 2026-10（月）。
    """
    c = conn()
    try:
        sql = ('SELECT l.cost, l.return_amount, '
               'COALESCE(MIN(t.created_at), l.created_at) AS bet_at '
               'FROM ledger l LEFT JOIN tickets t '
               '  ON t.user_id=l.user_id AND t.lottery=l.lottery AND t.issue=l.issue '
               'WHERE l.user_id=?')
        args = [user_id]
        if lottery:
            sql += ' AND l.lottery=?'
            args.append(lottery)
        sql += ' GROUP BY l.id'
        rows = c.execute(sql, args).fetchall()
        buckets = {}
        for r in rows:
            bet_at = r['bet_at'] or ''
            d = ((bet_at or '')[:7] if by == 'month' else (bet_at or '')[:10]) or '未知'
            b = buckets.setdefault(d, [0.0, 0.0])
            b[0] += float(r['cost'] or 0)
            b[1] += float(r['return_amount'] or 0)
        out = [{'date': k, 'cost': round(v[0], 2), 'ret': round(v[1], 2),
                'net': round(v[1] - v[0], 2)} for k, v in buckets.items()]
        out.sort(key=lambda x: x['date'])
        return out
    finally:
        c.close()


def unsettled(user_id=None):
    c = conn()
    try:
        sql = 'SELECT * FROM ledger WHERE settled=0'
        args = []
        if user_id:
            sql += ' AND user_id=?'
            args.append(user_id)
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def mark_settled(lid, ret):
    c = conn()
    c.execute('UPDATE ledger SET return_amount=?, settled=1 WHERE id=?', (ret, lid))
    c.commit()
    c.close()


# ───────────────────────── 配置 ─────────────────────────

def set_setting(key, value):
    c = conn()
    c.execute('INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) '
              'ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
              (key, json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value, now()))
    c.commit()
    c.close()


def get_setting(key, default=None):
    c = conn()
    try:
        r = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        if not r:
            return default
        v = r[0]
        try:
            return json.loads(v)
        except Exception:
            return v
    finally:
        c.close()


def set_user_setting(uid, key, value):
    c = conn()
    c.execute('INSERT INTO user_settings (user_id, key, value) VALUES (?,?,?) '
              'ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value',
              (uid, key, value))
    c.commit()
    c.close()


def get_user_setting(uid, key, default=None):
    c = conn()
    try:
        r = c.execute('SELECT value FROM user_settings WHERE user_id=? AND key=?', (uid, key)).fetchone()
        return r[0] if r else default
    finally:
        c.close()


def stats():
    c = conn()
    try:
        n = c.execute('SELECT COUNT(*) FROM draws').fetchone()[0]
        per = [dict(r) for r in c.execute(
            'SELECT lottery, COUNT(*) n, MIN(issue) a, MAX(issue) b FROM draws GROUP BY lottery ORDER BY lottery')]
        return dict(total=n, per=per,
                    users=c.execute('SELECT COUNT(*) FROM users').fetchone()[0],
                    tickets=c.execute('SELECT COUNT(*) FROM tickets').fetchone()[0])
    finally:
        c.close()
