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
import hmac
import hashlib
import base64
import gzip
import uuid
import sqlite3
import threading
import subprocess
import time
import openpyxl
import urllib.request
import urllib.error
from urllib.parse import urlparse, urlencode

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

# ── 写保护：足迹类 key 的写操作必须带口令 ─────────────────────────────────────
# 只拦写（save/delete）与 load 的 default 落库；读操作保持开放，
# 这样任何设备打开页面都能看到清单，只有改动才需要口令。
PROTECTED_WRITE_RE = re.compile(r'^(?:world)?footprint(?:_|$)')
TOKEN_FILE = os.path.join(ROOT, '.secrets', 'footprint_token')

def write_token():
    """口令来源：环境变量 FOOTPRINT_TOKEN 优先，其次 ROOT/.secrets/footprint_token。
    都读不到时返回空串 —— 此时所有受保护写操作一律拒绝（fail closed），不会静默放行。"""
    env = os.environ.get('FOOTPRINT_TOKEN', '').strip()
    if env:
        return env
    try:
        with open(TOKEN_FILE, 'r', encoding='utf-8') as fh:
            return fh.read().strip()
    except OSError:
        return ''

def is_protected_key(key):
    return bool(PROTECTED_WRITE_RE.match(str(key or '')))

# ── 足迹照片：上传到 nginx 静态目录，页面只存 URL ────────────────────────────
# 图片不进 SQLite（一张照片几百 KB，塞进 kv 会让每次读清单都拖上几 MB），
# 而是落盘成 /usr/share/nginx/html/footprint-images/<32位hex>.<ext>，由 nginx 直接当静态文件发。
# 写操作同样要口令（_require_token），空口令一律拒绝。
IMG_DIR = os.environ.get('FOOTPRINT_IMG_DIR', '/usr/share/nginx/html/footprint-images')
IMG_MAX = 8 * 1024 * 1024          # 单张上限 8 MB（客户端已压到 ~300 KB，这只是兜底）
IMG_URL_RE = re.compile(r'^/footprint-images/([0-9a-f]{32}\.(?:jpg|png|webp))$')

THUMB_PX, THUMB_Q = 800, 88


def _make_thumb(abs_path, url_path):
    """上传即生成 800px webp 缩略图（同生成库规格 .t800.webp）。失败返回 None 不阻断上传。"""
    try:
        from PIL import Image
        stem, _ = os.path.splitext(abs_path)
        thumb_abs = stem + '.t800.webp'
        if not os.path.exists(thumb_abs):
            with Image.open(abs_path) as im:
                im = im.convert('RGB')
                im.thumbnail((THUMB_PX, THUMB_PX))
                im.save(thumb_abs, 'WEBP', quality=THUMB_Q, method=4)
        return os.path.splitext(url_path)[0] + '.t800.webp'
    except Exception:
        return None


def img_save(raw, orig_name=''):
    """把 dataURL / 裸 base64 解码后落盘。按文件头判定真实类型，不信任客户端声明的 MIME。"""
    s = str(raw or '')
    if not s:
        return {'ok': False, 'error': 'missing data'}
    if s.startswith('data:'):
        head, _, payload = s.partition(',')
        if ';base64' not in head:
            return {'ok': False, 'error': 'not a base64 data url'}
        s = payload
    try:
        blob = base64.b64decode(s, validate=False)
    except Exception:
        return {'ok': False, 'error': 'bad base64'}
    n = len(blob)
    if not n:
        return {'ok': False, 'error': 'empty image'}
    if n > IMG_MAX:
        return {'ok': False, 'error': 'image too large'}
    if blob[:3] == b'\xff\xd8\xff':
        ext = '.jpg'
    elif blob[:8] == b'\x89PNG\r\n\x1a\n':
        ext = '.png'
    elif blob[:4] == b'RIFF' and blob[8:12] == b'WEBP':
        ext = '.webp'
    else:
        return {'ok': False, 'error': 'unsupported image type'}
    os.makedirs(IMG_DIR, exist_ok=True)
    fn = uuid.uuid4().hex + ext
    abs_path = os.path.join(IMG_DIR, fn)
    with open(abs_path, 'wb') as fh:
        fh.write(blob)
    url = '/footprint-images/' + fn
    # 数据升级：上传即生成 400px webp 缩略图（网格用缩略图、预览用原图）。失败不影响上传。
    thumb = _make_thumb(abs_path, url)
    return {'ok': True, 'url': url, 'thumb': thumb, 'size': n, 'name': str(orig_name or '')[:120]}

def img_delete(url):
    m = IMG_URL_RE.match(str(url or ''))
    if not m:
        return {'ok': False, 'error': 'bad url'}
    fp = os.path.join(IMG_DIR, m.group(1))
    try:
        if os.path.exists(fp):
            os.remove(fp)
    except OSError as e:
        return {'ok': False, 'error': str(e)}
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
        mods.insert(0, mod)
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

    def _require_token(self, data, label='protected'):
        """强制口令校验（不依赖 key 名，供 portal 等写接口使用）。
        通过返回 True；否则已回包并返回 False。fail closed：口令未配置时一律拒绝。"""
        expected = write_token()
        got = str(data.get('token', '') or self.headers.get('X-Footprint-Token', '') or '').strip()
        if not expected:
            print(f'[auth] {label}: 口令文件缺失/为空，拒绝写入', flush=True)
            self.send_json({'ok': False, 'error': 'server token not configured'}, 503)
            return False
        if not hmac.compare_digest(got, expected):
            print(f'[auth] {label}: 口令错误或未提供，拒绝写入（来自 {self.client_address[0]}）', flush=True)
            self.send_json({'ok': False, 'error': 'unauthorized', 'needToken': True}, 401)
            return False
        return True

    def _token_ok(self, data):
        """非阻断式口令判定：只返回 True/False，不回包。
        供「带口令才落库、不带口令静默降级为只读」的分支使用。"""
        expected = write_token()
        got = str(data.get('token', '') or self.headers.get('X-Footprint-Token', '') or '').strip()
        return bool(expected) and hmac.compare_digest(got, expected)

    def _check_write_auth(self, data):
        """受保护 key 的写操作鉴权：通过返回 True；否则已回包并返回 False。"""
        key = str(data.get('key', '') or '')
        if not is_protected_key(key):
            return True
        return self._require_token(data, key)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            # ── Legacy kv API (templates, processes, risks) ──
            if path == '/api/save':
                data = json.loads(self._read_body())
                if not self._require_token(data, 'save'):
                    return
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
                # default 只在带有效口令时才落库；不带口令仅作为返回值透传（页面行为不变）。
                # 否则未认证调用者可以凭 load 的 default 往 KV 里塞任意键值。
                if val is None and default is not None and not is_protected_key(key):
                    val = default
                    if self._token_ok(data):
                        db_save(key, default)
                self.send_json({'ok': True, 'value': val})

            elif path == '/api/keys':
                self.send_json({'ok': True, 'keys': db_keys()})

            elif path == '/api/delete':
                data = json.loads(self._read_body())
                if not self._require_token(data, 'delete'):
                    return
                db_delete(data.get('key', ''))
                self.send_json({'ok': True})

            # ── 足迹照片：上传 / 删除（都要口令；图片落 nginx 静态目录） ──
            elif path == '/api/footprint/upload':
                clen = int(self.headers.get('Content-Length', 0) or 0)
                if clen > IMG_MAX * 2:            # base64 膨胀 4/3，留一倍余量
                    self.send_json({'ok': False, 'error': 'payload too large'}, 413)
                    return
                data = json.loads(self._read_body() or '{}')
                if not self._require_token(data, 'upload'):
                    return
                res = img_save(data.get('data'), data.get('name'))
                self.send_json(res, 200 if res.get('ok') else 400)

            elif path == '/api/footprint/imgdel':
                data = json.loads(self._read_body() or '{}')
                if not self._require_token(data, 'imgdel'):
                    return
                res = img_delete(data.get('url'))
                self.send_json(res, 200 if res.get('ok') else 400)

            # ── Portal modules API (atomic single-item ops) ──
            elif path == '/api/portal/list':
                self.send_json(portal_list())

            elif path == '/api/portal/add':
                data = json.loads(self._read_body())
                if not self._require_token(data, 'portal/add'):
                    return
                self.send_json(portal_add(data.get('module') or data))

            elif path == '/api/portal/update':
                data = json.loads(self._read_body())
                if not self._require_token(data, 'portal/update'):
                    return
                self.send_json(portal_update(data.get('module') or data))

            elif path == '/api/portal/delete':
                data = json.loads(self._read_body())
                if not self._require_token(data, 'portal/delete'):
                    return
                self.send_json(portal_delete(data.get('id', '')))

            elif path == '/api/portal/move':
                data = json.loads(self._read_body())
                if not self._require_token(data, 'portal/move'):
                    return
                self.send_json(portal_move(data.get('id', ''), int(data.get('direction', 0))))

            else:
                self.send_error(404, 'Not found')

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_json({'ok': False, 'error': str(e)}, 500)

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        # 2026-11-13: 大 JSON（如「我的图库」2.7MB 清单）走 gzip，浏览器自动解压，
        # 传输体积降到 ~1/8；小于 2KB 的响应不压，避免小包反而变大。
        _gz = False
        if len(body) > 2048 and 'gzip' in (self.headers.get('Accept-Encoding') or '').lower():
            body = gzip.compress(body, 6)
            _gz = True
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        if _gz:
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Vary', 'Accept-Encoding')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Target-Url, X-Goog-Api-Key, X-Dashscope-Async, X-Footprint-Token')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PATCH, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Target-Url, X-Goog-Api-Key, X-Dashscope-Async, X-Footprint-Token')
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
