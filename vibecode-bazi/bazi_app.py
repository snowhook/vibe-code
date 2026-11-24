#!/usr/bin/env python3
"""vibecode-bazi 独立服务 — 拆分试点（2026-11-25）
只服务 /api/bazi/*；监听 127.0.0.1:5100，由 nginx 分流。
数据：data/bazi.db（bazi_records + kv）；报告 PDF 发布逻辑与主服务一致。
"""
import hmac
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)

import bazi_service
import bazi_report

TOKEN_FILE = '/home/admin/.openclaw/workspace/.secrets/footprint_token'


def write_token():
    env = os.environ.get('FOOTPRINT_TOKEN', '').strip()
    if env:
        return env
    try:
        return open(TOKEN_FILE, encoding='utf-8').read().strip()
    except OSError:
        return ''


_db_lock = threading.Lock()


def _conn():
    os.makedirs(os.path.dirname(bazi_service.DB_PATH), exist_ok=True)
    import sqlite3
    conn = sqlite3.connect(bazi_service.DB_PATH, timeout=15)
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
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False)))
        conn.commit()
        conn.close()


class Handler(BaseHTTPRequestHandler):
    server_version = 'vibecode-bazi/1.0'

    def _send(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode('utf-8')
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
            if path == '/api/bazi/cities':
                self._send({'ok': True, 'provinces': bazi_service.get_cities()})

            elif path == '/api/bazi/lunar-days':
                data = json.loads(self._read_body())
                y, m = int(data.get('year', 2000)), int(data.get('month', 1))
                self._send({'ok': True,
                            'days': bazi_service.lunar_month_days(y, m, bool(data.get('is_leap'))),
                            'has_leap': bazi_service.has_leap_month(y, m)})

            elif path == '/api/bazi/focus-keywords':
                self._send(bazi_service.get_focus_keywords(db_load, db_save))

            elif path == '/api/bazi/focus-keywords/save':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                self._send(bazi_service.save_focus_keywords(db_save, data.get('keywords', [])))

            elif path == '/api/bazi/analyze':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                try:
                    result = bazi_service.analyze(data)
                except Exception as e:
                    self._send({'ok': False, 'error': str(e)}, 400)
                    return
                rec_id = str(data.get('update_id') or '') or ('bazi_' + str(int(time.time() * 1000)))
                bazi_service.save_record(rec_id, data, result)
                try:
                    bazi_report.clear_report(rec_id)
                except Exception:
                    pass
                result['id'] = rec_id
                self._send(result)

            elif path == '/api/bazi/report':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                self._send(bazi_report.build_report(str(data.get('id', '')),
                                                    regenerate=bool(data.get('regenerate'))))

            elif path == '/api/bazi/report/simple':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                self._send(bazi_report.build_simple(str(data.get('id', '')),
                                                    regenerate=bool(data.get('regenerate'))))

            elif path == '/api/bazi/report/simple-pdf':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                self._send(bazi_report.build_simple_pdf(str(data.get('id', '')),
                                                        regenerate=bool(data.get('regenerate'))))

            elif path == '/api/bazi/report/html':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                self._send(bazi_report.build_full_page(str(data.get('id', '')),
                                                       regenerate=bool(data.get('regenerate'))))

            elif path == '/api/bazi/records':
                self._send(bazi_service.list_records())

            elif path == '/api/bazi/record':
                data = json.loads(self._read_body())
                self._send(bazi_service.get_record(str(data.get('id', ''))))

            elif path == '/api/bazi/record/delete':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                self._send(bazi_service.delete_record(str(data.get('id', ''))))

            elif path == '/api/bazi/pdf':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                self._send(bazi_report.build_pdf(str(data.get('id', '')),
                                                 regenerate=bool(data.get('regenerate'))))

            elif path == '/api/bazi/health':
                self._send({'ok': True, 'service': 'vibecode-bazi', 'ts': time.time()})

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
    srv = ThreadingHTTPServer(('127.0.0.1', 5100), Handler)
    srv.daemon_threads = True
    print('vibecode-bazi listening 127.0.0.1:5100 db=%s' % bazi_service.DB_PATH, flush=True)
    srv.serve_forever()
