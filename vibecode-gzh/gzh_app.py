#!/usr/bin/env python3
"""vibecode-gzh 独立服务 — 公众号热门文章（拆分 2026-11-25）
只服务 /api/gzh/*；监听 127.0.0.1:5102，由 nginx 分流。
"""
import hmac
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)
import gzh_trends
TOKEN_FILE = '/home/admin/.openclaw/workspace/.secrets/footprint_token'


def write_token():
    env = os.environ.get('FOOTPRINT_TOKEN', '').strip()
    if env:
        return env
    try:
        return open(TOKEN_FILE, encoding='utf-8').read().strip()
    except OSError:
        return ''


import sqlite3
import threading
_db_lock = threading.Lock()
DB_PATH = os.path.join(APP, 'data', 'gzh.db')


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute('CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)')
    return conn


def db_load(key):
    with _db_lock:
        conn = _conn()
        row = conn.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        conn.close()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None


def db_save(key, value):
    with _db_lock:
        conn = _conn()
        conn.execute(
            'INSERT INTO kv(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) '
            'ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
            (key, json.dumps(value, ensure_ascii=False)))
        conn.commit()
        conn.close()


class Handler(BaseHTTPRequestHandler):
    server_version = 'vibecode-gzh/1.0'

    def _send(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self):
        n = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(n).decode('utf-8') if n else '{}'

    def _token_ok(self, data):
        expected = write_token()
        got = str(data.get('token', '') or self.headers.get('X-Footprint-Token', '') or '').strip()
        if not expected:
            self._send({'ok': False, 'error': 'server token not configured'}, 503)
            return False
        if not hmac.compare_digest(got, expected):
            self._send({'ok': False, 'error': 'unauthorized', 'needToken': True}, 401)
            return False
        return True

    def do_POST(self):
        path = self.path.split('?')[0]
        try:
            if path == '/api/gzh/trending':
                data = json.loads(self._read_body()) if self.headers.get('Content-Length', '0') != '0' else {}
                days = max(1, min(int(data.get('days', 2)), 30))
                limit = min(int(data.get('limit', 10)), 50)
                offset = max(int(data.get('offset', 0)), 0)
                self._send(gzh_trends.fetch_trending(days=days, limit=limit, offset=offset))

            elif path == '/api/gzh/search':
                data = json.loads(self._read_body())
                keyword = str(data.get('keyword', '')).strip()
                days = max(1, min(int(data.get('days', 7)), 30))
                limit = min(int(data.get('limit', 10)), 50)
                offset = max(int(data.get('offset', 0)), 0)
                self._send(gzh_trends.search(keyword=keyword, days=days, limit=limit, offset=offset))

            elif path == '/api/gzh/keywords/get':
                val = db_load('gzh_keywords')
                self._send({'ok': True, 'keywords': val if isinstance(val, list) else []})

            elif path == '/api/gzh/keywords/save':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                kws = data.get('keywords', [])
                if not isinstance(kws, list):
                    kws = []
                db_save('gzh_keywords', kws)
                self._send({'ok': True})

            elif path == '/api/gzh/author-rank':
                data = json.loads(self._read_body()) if self.headers.get('Content-Length', '0') != '0' else {}
                days = max(1, min(int(data.get('days', 7)), 30))
                self._send(gzh_trends.author_rank(days=days))
            elif path == '/api/gzh/health':
                self._send({'ok': True, 'service': 'vibecode-gzh', 'ts': time.time()})
            else:
                self._send({'ok': False, 'error': 'not found'}, 404)
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self._send({'ok': False, 'error': str(e)}, 500)
            except Exception:
                pass

    def log_message(self, fmt, *args):
        print('[%s] %s' % (time.strftime('%F %T'), fmt % args), flush=True)


if __name__ == '__main__':
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer(('127.0.0.1', 5102), Handler)
    srv.daemon_threads = True
    print('vibecode-gzh listening 127.0.0.1:5102', flush=True)
    srv.serve_forever()
