#!/usr/bin/env python3
"""vibecode-lockup 独立服务 — 解禁预警雷达（拆分 2026-11-25）
只服务 /api/lockup/*；监听 127.0.0.1:5104，由 nginx 分流。
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
import lockup_radar
TOKEN_FILE = '/home/admin/.openclaw/workspace/.secrets/footprint_token'


def write_token():
    env = os.environ.get('FOOTPRINT_TOKEN', '').strip()
    if env:
        return env
    try:
        return open(TOKEN_FILE, encoding='utf-8').read().strip()
    except OSError:
        return ''


class Handler(BaseHTTPRequestHandler):
    server_version = 'vibecode-lockup/1.0'

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
            if path == '/api/lockup/data':
                data = json.loads(self._read_body()) if int(self.headers.get('Content-Length', 0)) else {}
                base = str(data.get('base', '')).strip()
                days = max(7, min(int(data.get('days', 90)), 365))
                from datetime import datetime as _dt
                if not re.match(r'^\d{4}-\d{2}-\d{2}$', base):
                    base = _dt.now().strftime('%Y-%m-%d')
                self._send({'ok': True, 'data': lockup_radar.build_data(base, days)})
            elif path == '/api/lockup/health':
                self._send({'ok': True, 'service': 'vibecode-lockup', 'ts': time.time()})
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
    srv = ThreadingHTTPServer(('127.0.0.1', 5104), Handler)
    srv.daemon_threads = True
    print('vibecode-lockup listening 127.0.0.1:5104', flush=True)
    srv.serve_forever()
