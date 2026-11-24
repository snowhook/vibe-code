#!/usr/bin/env python3
"""vibecode-math 独立服务 — 动画化解题（拆分 2026-11-25）
只服务 /api/math/*；监听 127.0.0.1:5101，由 nginx 分流。
数据：data/math_videos/（文件型，record.json/index.json），
发布目录不变：/usr/share/nginx/html/math-videos/ 与 math-exports/。
"""
import hmac
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)

import math_solver

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
    server_version = 'vibecode-math/1.0'

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
            if path == '/api/math/recognize':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                self._send(math_solver.recognize_image(data.get('image', '')))

            elif path == '/api/math/solve':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                self._send(math_solver.solve_problem(
                    data.get('problem', ''), data.get('subject'), data.get('image'),
                    data.get('note')))

            elif path == '/api/math/chat':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                self._send(math_solver.chat(data.get('id', ''), data.get('message', '')))

            elif path == '/api/math/export':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                self._send(math_solver.export_html(data.get('id', '')))

            elif path == '/api/math/animate':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                self._send(math_solver.animate(data.get('id', ''), data.get('voice')))

            elif path == '/api/math/voices':
                self._send(math_solver.voice_options())

            elif path == '/api/math/anim-status':
                data = json.loads(self._read_body())
                self._send(math_solver.anim_status(data.get('id', '')))

            elif path == '/api/math/history':
                self._send(math_solver.history_list())

            elif path == '/api/math/record':
                data = json.loads(self._read_body())
                self._send(math_solver.history_get(data.get('id', '')))

            elif path == '/api/math/delete':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                self._send(math_solver.history_delete(data.get('id', '')))

            elif path == '/api/math/health':
                self._send({'ok': True, 'service': 'vibecode-math', 'ts': time.time()})

            else:
                self._send({'ok': False, 'error': 'not found'}, 404)
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self._send({'ok': False, 'error': str(e)}, 500)
            except Exception:
                pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/math/image':
            try:
                rid = (parse_qs(parsed.query).get('id') or [''])[0]
                raw, mime = math_solver.get_image_bytes(rid)
                if raw is None:
                    self.send_error(404, 'Image not found')
                    return
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'max-age=86400')
                self.end_headers()
                self.wfile.write(raw)
            except Exception:
                self.send_error(500)
            return
        if parsed.path == '/api/math/health':
            self._send({'ok': True, 'service': 'vibecode-math', 'ts': time.time()})
            return
        self.send_error(405, 'Method Not Allowed')

    def log_message(self, fmt, *args):
        print('[%s] %s' % (time.strftime('%F %T'), fmt % args), flush=True)


if __name__ == '__main__':
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer(('127.0.0.1', 5101), Handler)
    srv.daemon_threads = True
    print('vibecode-math listening 127.0.0.1:5101 base=%s' % math_solver.BASE_DIR, flush=True)
    srv.serve_forever()
