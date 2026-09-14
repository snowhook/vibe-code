#!/usr/bin/env python3
"""Static file server + data persistence API for routing.html
Storage: SQLite (routing.db)
  - kv table: templates, processes, risks (key-value)
  - routings + routing_details tables: normalized routing storage with pagination
"""
import http.server
import json
import os
import sys
import io
import re
import sqlite3
import threading
import subprocess
import openpyxl
from urllib.parse import urlparse

PORT = 8765
ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'routing.db')
LEGACY_DIR = os.path.join(ROOT, '.routing-data')

# ── SQLite setup ──────────────────────────────────────────────────────────────

_db_lock = threading.Lock()

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=5000')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn

def init_db():
    conn = get_conn()
    # Legacy kv table (templates, processes, risks)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS kv (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Normalized routing tables
    conn.execute('''
        CREATE TABLE IF NOT EXISTS routings (
            eid        TEXT PRIMARY KEY,
            name       TEXT NOT NULL DEFAULT '',
            mode       TEXT NOT NULL DEFAULT 'template',
            type       TEXT NOT NULL DEFAULT 'single',
            parts_json TEXT NOT NULL DEFAULT '[]',
            merges_json TEXT NOT NULL DEFAULT '[]',
            ts         TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS routing_details (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            routing_eid TEXT NOT NULL,
            step_key    TEXT NOT NULL,
            data_json   TEXT NOT NULL DEFAULT '{}',
            UNIQUE(routing_eid, step_key),
            FOREIGN KEY (routing_eid) REFERENCES routings(eid) ON DELETE CASCADE
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_rd_eid ON routing_details(routing_eid)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_routings_name ON routings(name)')
    conn.commit()

    # ── Migration 1: legacy JSON files → kv ──
    if os.path.isdir(LEGACY_DIR):
        migrated = 0
        for fn in os.listdir(LEGACY_DIR):
            if not fn.endswith('.json'):
                continue
            key = fn[:-5]
            cur = conn.execute('SELECT 1 FROM kv WHERE key=?', (key,))
            if cur.fetchone():
                continue
            fp = os.path.join(LEGACY_DIR, fn)
            try:
                with open(fp, 'r', encoding='utf-8') as f:
                    raw = f.read()
                json.loads(raw)
                conn.execute('INSERT OR IGNORE INTO kv (key, value) VALUES (?, ?)', (key, raw))
                migrated += 1
            except Exception as e:
                print(f'  skip {fn}: {e}')
        if migrated:
            conn.commit()
            print(f'Migrated {migrated} legacy JSON files into kv.')

    # ── Migration 2: kv pt_routings + pt_pd_* → routings + routing_details ──
    cur = conn.execute("SELECT 1 FROM routings LIMIT 1")
    if cur.fetchone() is None:
        # routings table is empty → try migrating from kv
        cur = conn.execute("SELECT value FROM kv WHERE key='pt_routings'")
        row = cur.fetchone()
        if row:
            try:
                all_routings = json.loads(row[0])
                count = 0
                for r in all_routings:
                    eid = r.get('eid', '')
                    if not eid:
                        continue
                    conn.execute(
                        'INSERT OR IGNORE INTO routings (eid, name, mode, type, parts_json, merges_json, ts) '
                        'VALUES (?,?,?,?,?,?,?)',
                        (eid, r.get('name',''), r.get('mode','template'),
                         r.get('type','single'),
                         json.dumps(r.get('parts',[]), ensure_ascii=False),
                         json.dumps(r.get('merges',[]), ensure_ascii=False),
                         r.get('ts'))
                    )
                    # Load details from kv pt_pd_<eid>
                    cur2 = conn.execute("SELECT value FROM kv WHERE key=?", ('pt_pd_'+eid,))
                    row2 = cur2.fetchone()
                    if row2:
                        try:
                            details = json.loads(row2[0])
                            for step_key, step_data in details.items():
                                conn.execute(
                                    'INSERT OR IGNORE INTO routing_details (routing_eid, step_key, data_json) '
                                    'VALUES (?,?,?)',
                                    (eid, step_key, json.dumps(step_data, ensure_ascii=False))
                                )
                        except:
                            pass
                    count += 1
                conn.commit()
                print(f'Migrated {count} routings from kv to normalized tables.')
            except Exception as e:
                print(f'Routing migration error: {e}')
                conn.rollback()

    conn.close()

# ── kv helpers (templates, processes, risks) ─────────────────────────────────

def db_load(key):
    with _db_lock:
        conn = get_conn()
        cur = conn.execute('SELECT value FROM kv WHERE key=?', (key,))
        row = cur.fetchone()
        conn.close()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except:
            return None

def db_save(key, value):
    raw = json.dumps(value, ensure_ascii=False)
    with _db_lock:
        conn = get_conn()
        conn.execute(
            'INSERT INTO kv (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) '
            'ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
            (key, raw)
        )
        conn.commit()
        conn.close()
    return True

def db_delete(key):
    with _db_lock:
        conn = get_conn()
        conn.execute('DELETE FROM kv WHERE key=?', (key,))
        conn.commit()
        conn.close()

def db_keys():
    with _db_lock:
        conn = get_conn()
        cur = conn.execute('SELECT key FROM kv ORDER BY key')
        keys = [r[0] for r in cur.fetchall()]
        conn.close()
        return keys

# ── Routing helpers (normalized tables) ──────────────────────────────────────

def routing_list(page=1, size=50, search=''):
    with _db_lock:
        conn = get_conn()
        offset = (max(1, page) - 1) * size
        if search:
            like = f'%{search}%'
            total = conn.execute('SELECT COUNT(*) FROM routings WHERE name LIKE ?', (like,)).fetchone()[0]
            cur = conn.execute(
                'SELECT eid, name, mode, type, parts_json, ts FROM routings '
                'WHERE name LIKE ? ORDER BY updated_at DESC LIMIT ? OFFSET ?',
                (like, size, offset)
            )
        else:
            total = conn.execute('SELECT COUNT(*) FROM routings').fetchone()[0]
            cur = conn.execute(
                'SELECT eid, name, mode, type, parts_json, ts FROM routings '
                'ORDER BY updated_at DESC LIMIT ? OFFSET ?',
                (size, offset)
            )
        items = []
        for r in cur.fetchall():
            try:
                parts = json.loads(r[4])
            except:
                parts = []
            items.append({
                'eid': r[0], 'name': r[1], 'mode': r[2], 'type': r[3],
                'parts': parts, 'ts': r[5]
            })
        conn.close()
        return {'ok': True, 'items': items, 'total': total, 'page': page, 'size': size}

def routing_get(eid):
    with _db_lock:
        conn = get_conn()
        cur = conn.execute(
            'SELECT eid, name, mode, type, parts_json, merges_json, ts FROM routings WHERE eid=?',
            (eid,)
        )
        r = cur.fetchone()
        if not r:
            conn.close()
            return {'ok': False, 'error': 'Not found'}
        try:
            parts = json.loads(r[4])
        except:
            parts = []
        try:
            merges = json.loads(r[5])
        except:
            merges = []
        # Load details
        cur2 = conn.execute('SELECT step_key, data_json FROM routing_details WHERE routing_eid=?', (eid,))
        details = {}
        for dr in cur2.fetchall():
            try:
                details[dr[0]] = json.loads(dr[1])
            except:
                details[dr[0]] = {}
        conn.close()
        return {
            'ok': True,
            'routing': {
                'eid': r[0], 'name': r[1], 'mode': r[2], 'type': r[3],
                'parts': parts, 'merges': merges, 'ts': r[6]
            },
            'details': details
        }

def routing_save(data):
    """Upsert a routing + replace its details. Returns eid."""
    eid = data.get('eid') or str(int(__import__('time').time() * 1000))
    name = data.get('name', '')
    mode = data.get('mode', 'template')
    rtype = data.get('type', 'single')
    parts = data.get('parts', [])
    merges = data.get('merges', [])
    ts = data.get('ts') or __import__('datetime').datetime.now().isoformat()
    details = data.get('details')  # optional: dict of step_key → data

    with _db_lock:
        conn = get_conn()
        conn.execute(
            'INSERT INTO routings (eid, name, mode, type, parts_json, merges_json, ts, updated_at) '
            'VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP) '
            'ON CONFLICT(eid) DO UPDATE SET '
            'name=excluded.name, mode=excluded.mode, type=excluded.type, '
            'parts_json=excluded.parts_json, merges_json=excluded.merges_json, '
            'ts=excluded.ts, updated_at=CURRENT_TIMESTAMP',
            (eid, name, mode, rtype,
             json.dumps(parts, ensure_ascii=False),
             json.dumps(merges, ensure_ascii=False), ts)
        )
        if details is not None:
            conn.execute('DELETE FROM routing_details WHERE routing_eid=?', (eid,))
            for step_key, step_data in details.items():
                conn.execute(
                    'INSERT INTO routing_details (routing_eid, step_key, data_json) VALUES (?,?,?)',
                    (eid, step_key, json.dumps(step_data, ensure_ascii=False))
                )
        conn.commit()
        conn.close()
    return {'ok': True, 'eid': eid}

def routing_delete(eid):
    with _db_lock:
        conn = get_conn()
        conn.execute('DELETE FROM routing_details WHERE routing_eid=?', (eid,))
        conn.execute('DELETE FROM routings WHERE eid=?', (eid,))
        conn.commit()
        conn.close()
    return {'ok': True}

# ── Portal modules (atomic single-item ops, no full-list overwrite) ─────────

PORTAL_KEY = 'portal_modules'

def _portal_read(conn):
    cur = conn.execute('SELECT value FROM kv WHERE key=?', (PORTAL_KEY,))
    row = cur.fetchone()
    if row is None:
        return []
    try:
        v = json.loads(row[0])
        return v if isinstance(v, list) else []
    except:
        return []

def _portal_write(conn, mods):
    conn.execute(
        'INSERT INTO kv (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) '
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP',
        (PORTAL_KEY, json.dumps(mods, ensure_ascii=False))
    )

def portal_list():
    with _db_lock:
        conn = get_conn()
        mods = _portal_read(conn)
        conn.close()
    return {'ok': True, 'modules': mods}

def portal_add(mod):
    if not mod or not mod.get('name') or not mod.get('url'):
        return {'ok': False, 'error': 'Missing name or url'}
    if not mod.get('id'):
        mod['id'] = 'm_' + str(int(__import__('time').time() * 1000))
    with _db_lock:
        conn = get_conn()
        mods = _portal_read(conn)
        # 同 id 已存在则拒绝，避免重复
        if any(m.get('id') == mod['id'] for m in mods):
            conn.close()
            return {'ok': False, 'error': 'Module id already exists'}
        mods.append(mod)
        _portal_write(conn, mods)
        conn.commit()
        conn.close()
    return {'ok': True, 'module': mod}

def portal_update(mod):
    mid = mod.get('id') if mod else None
    if not mid:
        return {'ok': False, 'error': 'Missing id'}
    with _db_lock:
        conn = get_conn()
        mods = _portal_read(conn)
        found = False
        for i, m in enumerate(mods):
            if m.get('id') == mid:
                merged = dict(m)
                merged.update(mod)
                mods[i] = merged
                found = True
                break
        if not found:
            conn.close()
            return {'ok': False, 'error': 'Module not found'}
        _portal_write(conn, mods)
        conn.commit()
        conn.close()
    return {'ok': True}

def portal_delete(mid):
    with _db_lock:
        conn = get_conn()
        mods = _portal_read(conn)
        mods = [m for m in mods if m.get('id') != mid]
        _portal_write(conn, mods)
        conn.commit()
        conn.close()
    return {'ok': True}

def portal_move(mid, direction):
    with _db_lock:
        conn = get_conn()
        mods = _portal_read(conn)
        idx = next((i for i, m in enumerate(mods) if m.get('id') == mid), -1)
        j = idx + direction
        if idx < 0 or j < 0 or j >= len(mods):
            conn.close()
            return {'ok': False, 'error': 'Cannot move'}
        mods[idx], mods[j] = mods[j], mods[idx]
        _portal_write(conn, mods)
        conn.commit()
        conn.close()
    return {'ok': True}

# ── Stock reports (文件存于 stock-reports/，元信息存 DB) ──────────────────

REPORTS_DIR = os.path.join(ROOT, 'stock-reports')
GENERATOR = os.path.join(ROOT, 'generate_stock_report.py')
REPORTS_KEY = 'stock_reports'
# nginx 对外发布目录（生成后自动同步，供公网下载）
REPORTS_PUBLISH_DIR = '/usr/share/nginx/html/stock-reports'


def _publish(filename):
    """将报告文件同步到 nginx 发布目录（失败不阻断主流程）"""
    try:
        os.makedirs(REPORTS_PUBLISH_DIR, exist_ok=True)
        src = os.path.join(REPORTS_DIR, filename)
        dst = os.path.join(REPORTS_PUBLISH_DIR, filename)
        if os.path.exists(src):
            import shutil
            shutil.copy2(src, dst)
            os.chmod(dst, 0o755)
    except Exception as e:
        print(f'publish sync failed for {filename}: {e}')


def _reports_read(conn):
    cur = conn.execute('SELECT value FROM kv WHERE key=?', (REPORTS_KEY,))
    row = cur.fetchone()
    if row is None:
        return []
    try:
        v = json.loads(row[0])
        return v if isinstance(v, list) else []
    except:
        return []


def _reports_write(conn, items):
    conn.execute(
        'INSERT INTO kv (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) '
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP',
        (REPORTS_KEY, json.dumps(items, ensure_ascii=False))
    )


def _report_title(filename):
    """从 HTML 文件提取 <title>"""
    fp = os.path.join(REPORTS_DIR, filename)
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            head = f.read(4096)
        m = re.search(r'<title>([^<]*)</title>', head)
        return m.group(1).strip() if m else filename
    except:
        return filename


def reports_list():
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with _db_lock:
        conn = get_conn()
        meta_by_file = {it.get('filename'): it for it in _reports_read(conn)}
        conn.close()
    items = []
    for fn in os.listdir(REPORTS_DIR):
        if not fn.endswith('.html'):
            continue
        fp = os.path.join(REPORTS_DIR, fn)
        mtime_ms = int(os.path.getmtime(fp) * 1000)
        meta = meta_by_file.get(fn)
        if meta:
            item = dict(meta)
        else:
            # 从文件推导最小元信息
            code = fn.split('_')[0]
            item = {
                'filename': fn,
                'code': code,
                'name': _report_title(fn),
                'ts': mtime_ms,
                'gen_time': __import__('datetime').datetime.fromtimestamp(mtime_ms / 1000).strftime('%Y-%m-%d %H:%M:%S'),
                'rating': '',
            }
        item.setdefault('ts', mtime_ms)
        items.append(item)
    items.sort(key=lambda x: x.get('ts', 0), reverse=True)
    return {'ok': True, 'reports': items}


def reports_generate(code, market):
    if not re.match(r'^\d{5,6}$', code):
        return {'ok': False, 'error': '股票代码格式不正确（应为5-6位数字）'}
    try:
        proc = subprocess.run(
            [sys.executable, GENERATOR, code, '--market', market],
            capture_output=True, text=True, timeout=240, cwd=ROOT
        )
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': '生成超时（数据源响应过慢），请稍后重试'}
    # 从 stdout 提取 JSON 元信息
    meta = None
    for line in (proc.stdout or '').splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                meta = json.loads(line)
            except:
                pass
    if not meta or not meta.get('ok'):
        err = (meta or {}).get('error') or (proc.stderr or '').strip().splitlines()[-1:] or ['未知错误']
        err = err if isinstance(err, str) else err[0]
        return {'ok': False, 'error': f'生成失败: {err}'}
    # 写入元信息库
    item = {
        'filename': meta['filename'],
        'code': meta.get('code', code),
        'name': meta.get('name', ''),
        'ts': meta.get('ts', 0),
        'gen_time': meta.get('gen_time', ''),
        'price': meta.get('price'),
        'rating': meta.get('rating', ''),
        'pe': meta.get('pe'),
        'pb': meta.get('pb'),
        'total_mv': meta.get('total_mv'),
        'value_range': meta.get('value_range', ''),
    }
    with _db_lock:
        conn = get_conn()
        items = _reports_read(conn)
        items = [it for it in items if it.get('filename') != item['filename']]
        items.append(item)
        _reports_write(conn, items)
        conn.commit()
        conn.close()
    _publish(item['filename'])
    return {'ok': True, 'report': item}


def reports_delete(filename):
    # 防路径穿越
    if not re.match(r'^[\w\-\.]+\.html$', filename) or '..' in filename:
        return {'ok': False, 'error': '非法文件名'}
    fp = os.path.join(REPORTS_DIR, filename)
    if os.path.exists(fp):
        os.remove(fp)
    # 同步删除发布目录中的 HTML 和 PDF
    for suffix in ('.html', '.pdf'):
        pub = os.path.join(REPORTS_PUBLISH_DIR, filename[:-5] + suffix)
        if os.path.exists(pub):
            try:
                os.remove(pub)
            except Exception:
                pass
    with _db_lock:
        conn = get_conn()
        items = _reports_read(conn)
        items = [it for it in items if it.get('filename') != filename]
        _reports_write(conn, items)
        conn.commit()
        conn.close()
    return {'ok': True}


def reports_pdf(filename):
    """将报告 HTML 转为 PDF（带缓存），返回可下载的 PDF 文件名"""
    if not re.match(r'^[\w\-\.]+\.html$', filename) or '..' in filename:
        return {'ok': False, 'error': '非法文件名'}
    src = os.path.join(REPORTS_DIR, filename)
    if not os.path.exists(src):
        return {'ok': False, 'error': '报告不存在'}
    pdf_name = filename[:-5] + '.pdf'
    pdf_path = os.path.join(REPORTS_DIR, pdf_name)
    # 缓存：HTML 未更新且 PDF 已存在则直接返回
    if os.path.exists(pdf_path) and os.path.getmtime(pdf_path) >= os.path.getmtime(src):
        _publish(pdf_name)
        return {'ok': True, 'pdf': pdf_name, 'cached': True}
    try:
        import weasyprint
        weasyprint.HTML(filename=src).write_pdf(pdf_path)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {'ok': False, 'error': f'PDF 生成失败: {e}'}
    _publish(pdf_name)
    return {'ok': True, 'pdf': pdf_name, 'cached': False}


# ── HTTP Server ───────────────────────────────────────────────────────────────

class ThreadingHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(http.server.SimpleHTTPRequestHandler):
    timeout = 30

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(length).decode('utf-8')

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            # ── Legacy kv API (templates, processes, risks) ──
            if path == '/api/save':
                data = json.loads(self._read_body())
                key = data.get('key', 'default')
                value = data.get('value')
                if not key or value is None:
                    self.send_json({'ok': False, 'error': 'Missing key or value'}, 400)
                    return
                db_save(key, value)
                self.send_json({'ok': True})

            elif path == '/api/load':
                data = json.loads(self._read_body())
                key = data.get('key', 'default')
                default = data.get('default')
                val = db_load(key)
                if val is None and default is not None:
                    val = default
                    db_save(key, default)
                self.send_json({'ok': True, 'value': val})

            elif path == '/api/keys':
                self.send_json({'ok': True, 'keys': db_keys()})

            elif path == '/api/delete':
                data = json.loads(self._read_body())
                db_delete(data.get('key', ''))
                self.send_json({'ok': True})

            # ── 市场情绪日报: 龙虎榜+行业/概念板块 ──
            elif path == '/api/sentiment/data':
                data = json.loads(self._read_body()) if int(self.headers.get('Content-Length', 0)) else {}
                date = str(data.get('date', '')).strip()
                try:
                    import sentiment_daily
                    from datetime import datetime as _dt
                    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
                        date = _dt.now().strftime('%Y-%m-%d')
                    self.send_json({'ok': True, 'data': sentiment_daily.build_data(date)})
                except Exception as e:
                    self.send_json({'ok': False, 'error': str(e)}, 500)

            # ── 解禁预警雷达: 服务端实时取数 ──
            elif path == '/api/lockup/data':
                data = json.loads(self._read_body()) if int(self.headers.get('Content-Length', 0)) else {}
                base = str(data.get('base', '')).strip()
                days = int(data.get('days', 90))
                days = max(7, min(days, 365))
                try:
                    import lockup_radar
                    from datetime import datetime as _dt
                    if not re.match(r'^\d{4}-\d{2}-\d{2}$', base):
                        base = _dt.now().strftime('%Y-%m-%d')
                    self.send_json({'ok': True, 'data': lockup_radar.build_data(base, days)})
                except Exception as e:
                    self.send_json({'ok': False, 'error': str(e)}, 500)

            # ── Collab Express: real-time collab news fetch ──
            elif path == '/api/collab/fetch':
                try:
                    import collab_fetch
                    self.send_json(collab_fetch.fetch_all())
                except Exception as e:
                    self.send_json({'ok': False, 'error': str(e)}, 500)

            # ── Collab Express: 主区结构化联动（按今天刷新替换精选卡）──
            elif path == '/api/collab/structured':
                try:
                    import collab_fetch
                    self.send_json({'ok': True, 'data': collab_fetch.structured_collabs()})
                except Exception as e:
                    self.send_json({'ok': False, 'error': str(e)}, 500)

            # ── Routing API (normalized tables) ──
            elif path == '/api/routings/list':
                data = json.loads(self._read_body()) if self.headers.get('Content-Length', '0') != '0' else {}
                page = int(data.get('page', 1))
                size = min(int(data.get('size', 50)), 200)
                search = str(data.get('search', '')).strip()
                self.send_json(routing_list(page, size, search))

            elif path == '/api/routings/get':
                data = json.loads(self._read_body())
                eid = data.get('eid', '')
                self.send_json(routing_get(eid))

            elif path == '/api/routings/save':
                data = json.loads(self._read_body())
                self.send_json(routing_save(data))

            elif path == '/api/routings/delete':
                data = json.loads(self._read_body())
                eid = data.get('eid', '')
                self.send_json(routing_delete(eid))

            # ── Portal modules API (atomic single-item ops) ──
            elif path == '/api/portal/list':
                self.send_json(portal_list())

            elif path == '/api/portal/add':
                data = json.loads(self._read_body())
                self.send_json(portal_add(data.get('module') or data))

            elif path == '/api/portal/update':
                data = json.loads(self._read_body())
                self.send_json(portal_update(data.get('module') or data))

            elif path == '/api/portal/delete':
                data = json.loads(self._read_body())
                self.send_json(portal_delete(data.get('id', '')))

            elif path == '/api/portal/move':
                data = json.loads(self._read_body())
                self.send_json(portal_move(data.get('id', ''), int(data.get('direction', 0))))

            # ── Stock reports API ──
            elif path == '/api/reports/list':
                self.send_json(reports_list())

            elif path == '/api/reports/generate':
                data = json.loads(self._read_body())
                code = str(data.get('code', '')).strip()
                market = str(data.get('market', 'A')).strip() or 'A'
                self.send_json(reports_generate(code, market))

            elif path == '/api/reports/delete':
                data = json.loads(self._read_body())
                self.send_json(reports_delete(data.get('filename', '')))

            elif path == '/api/reports/pdf':
                data = json.loads(self._read_body())
                self.send_json(reports_pdf(data.get('filename', '')))

            # ── Excel parse ──
            elif path == '/api/parse-xls':
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length)
                wb = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
                ws = wb.active
                rows = []
                for row in ws.iter_rows(min_row=1, values_only=True):
                    if not row or not row[0]:
                        continue
                    name = str(row[0]).strip()
                    steps = [str(c).strip() for c in row[1:] if c is not None and str(c).strip()]
                    if steps:
                        rows.append({'name': name, 'steps': steps})
                self.send_json({'ok': True, 'rows': rows})

            else:
                self.send_error(404, 'Not found')

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_json({'ok': False, 'error': str(e)}, 500)

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, format, *args):
        if '/api/' in str(args[0] if args else ''):
            return
        super().log_message(format, *args)


if __name__ == '__main__':
    print(f'Initializing SQLite database: {DB_PATH}')
    init_db()
    print(f'Server starting at http://127.0.0.1:{PORT}/ (loopback only; nginx proxies /api/)')
    httpd = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nShutting down...')
        httpd.shutdown()
