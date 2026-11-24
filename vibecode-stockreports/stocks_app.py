#!/usr/bin/env python3
"""vibecode-stockreports 独立服务 — AI 股票分析报告（2026-11-25 从 server.py 拆分）
/api/reports/*（list/generate/delete/pdf）；监听 127.0.0.1:5107，nginx 前缀分流。
数据：data/stockreports.db（kv: stock_reports 元信息）+ data/stock-reports/（HTML/PDF 产物），
生成器 generate_stock_report.py 随 app 部署；发布目录不变 /usr/share/nginx/html/stock-reports。
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import hmac
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)

import sqlite3

DB_PATH = os.path.join(APP, 'data', 'stockreports.db')
REPORTS_DIR = os.path.join(APP, 'data', 'stock-reports')
GENERATOR = os.path.join(APP, 'generate_stock_report.py')
REPORTS_KEY = 'stock_reports'
# nginx 对外发布目录（生成后自动同步，供公网下载）——与拆分前一致
REPORTS_PUBLISH_DIR = '/usr/share/nginx/html/stock-reports'
TOKEN_FILE = '/home/admin/.openclaw/workspace/.secrets/footprint_token'
PORT = 5107
_db_lock = threading.Lock()


def write_token():
    env = os.environ.get('FOOTPRINT_TOKEN', '').strip()
    if env:
        return env
    try:
        return open(TOKEN_FILE, encoding='utf-8').read().strip()
    except OSError:
        return ''


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute('CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)')
    conn.row_factory = sqlite3.Row
    return conn


def _publish(filename):
    """报告文件同步到 nginx 发布目录（失败不阻断主流程）"""
    try:
        os.makedirs(REPORTS_PUBLISH_DIR, exist_ok=True)
        src = os.path.join(REPORTS_DIR, filename)
        dst = os.path.join(REPORTS_PUBLISH_DIR, filename)
        if os.path.exists(src):
            import shutil
            shutil.copy2(src, dst)
            os.chmod(dst, 0o755)
    except Exception as e:
        print(f'publish sync failed for {filename}: {e}', flush=True)


def _reports_read(conn):
    cur = conn.execute('SELECT value FROM kv WHERE key=?', (REPORTS_KEY,))
    row = cur.fetchone()
    if row is None:
        return []
    try:
        v = json.loads(row[0])
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _reports_write(conn, items):
    conn.execute(
        'INSERT INTO kv (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) '
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
        (REPORTS_KEY, json.dumps(items, ensure_ascii=False))
    )


def _report_title(filename):
    fp = os.path.join(REPORTS_DIR, filename)
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            head = f.read(4096)
        m = re.search(r'<title>([^<]*)</title>', head)
        return m.group(1).strip() if m else filename
    except Exception:
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
            capture_output=True, text=True, timeout=240, cwd=APP
        )
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': '生成超时（数据源响应过慢），请稍后重试'}
    meta = None
    for line in (proc.stdout or '').splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                meta = json.loads(line)
            except Exception:
                pass
    if not meta or not meta.get('ok'):
        err = (meta or {}).get('error') or (proc.stderr or '').strip().splitlines()[-1:] or ['未知错误']
        err = err if isinstance(err, str) else err[0]
        return {'ok': False, 'error': f'生成失败: {err}'}
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
    if not re.match(r'^[\w\-\.]+\.html$', filename) or '..' in filename:
        return {'ok': False, 'error': '非法文件名'}
    fp = os.path.join(REPORTS_DIR, filename)
    if os.path.exists(fp):
        os.remove(fp)
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
    if not re.match(r'^[\w\-\.]+\.html$', filename) or '..' in filename:
        return {'ok': False, 'error': '非法文件名'}
    src = os.path.join(REPORTS_DIR, filename)
    if not os.path.exists(src):
        return {'ok': False, 'error': '报告不存在'}
    pdf_name = filename[:-5] + '.pdf'
    pdf_path = os.path.join(REPORTS_DIR, pdf_name)
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


# ── 资本市场新闻：东财栏目取数（服务端代理，绕开浏览器 CORS 与 rss2json 停摆） ──
EM_NEWS_URL = 'https://np-listapi.eastmoney.com/comm/web/getNewsByColumns'
EM_FASTNEWS_URL = 'https://np-weblist.eastmoney.com/comm/web/getFastNewsList'
# 栏目是混合内容，港/美块以 7x24 快讯关键词分类为主、栏目为辅，避免 A 股新闻串块
EM_NEWS_COLS = {'a': [366, 353], 'hk': [354], 'us': [352]}
HK_KW_RE = re.compile(r'(港股|恒指|恒生指数|恒生科技|国企指数|H股|港股通|北水|南下资金|窝轮|牛证|熊证|香港交易所|港交所)')
US_KW_RE = re.compile(r'(美股|纳斯达克|道琼斯|道指|标普|美联储|美债|纽约|COMEX|英伟达|特斯拉|台积电|华尔街|美股本|在美|美国公司)')
_em_news_cache = {'ts': 0.0, 'data': None}
_EM_NEWS_TTL = 60

def _em_fastnews(page_size=50):
    params = urlencode({'client': 'web', 'biz': 'web_724', 'fastColumn': '102',
                        'sortEnd': '', 'pageSize': str(page_size),
                        'req_trace': str(int(time.time() * 1000))})
    req = urllib.request.Request(EM_FASTNEWS_URL + '?' + params,
                                 headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.loads(r.read().decode('utf-8'))
    items = []
    for it in (d.get('data') or {}).get('fastNewsList') or []:
        code = str(it.get('code') or '')
        title = (it.get('title') or '').strip()
        if not title or not code:
            continue
        items.append({'title': title, 'link': f'https://finance.eastmoney.com/a/{code}.html',
                      'pubDate': it.get('showTime') or '',
                      'src': '东财快讯',
                      '_body': title})
    return items

def _em_news_column(col, size=15):
    params = urlencode({'client': 'web', 'biz': 'web_news_col', 'column': str(col),
                        'order': '1', 'needInteractData': '0', 'page_index': '1',
                        'page_size': str(size), 'req_trace': str(int(time.time() * 1000))})
    req = urllib.request.Request(EM_NEWS_URL + '?' + params,
                                headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.loads(r.read().decode('utf-8'))
    items = []
    for it in (d.get('data') or {}).get('list') or []:
        code = str(it.get('code') or '')
        link = it.get('uniqueUrl') or (f'https://finance.eastmoney.com/a/{code}.html' if code else '')
        if link.startswith('http://'):
            link = 'https://' + link[7:]
        title = (it.get('title') or '').strip()
        if not title or not link:
            continue
        items.append({'title': title, 'link': link,
                      'pubDate': it.get('showTime') or '',
                      'src': (it.get('mediaName') or '东方财富').strip()})
    return items

def em_news_blocks():
    now = time.time()
    if _em_news_cache['data'] is not None and now - _em_news_cache['ts'] < _EM_NEWS_TTL:
        return _em_news_cache['data']
    out = {}
    try:
        fast = _em_fastnews(100)
    except Exception as e:
        print(f'[news] fastnews failed: {e}', flush=True)
        fast = []
    kw = {'hk': HK_KW_RE, 'us': US_KW_RE}
    caps = {'a': 30, 'hk': 10, 'us': 10}
    for key, cols in EM_NEWS_COLS.items():
        seen = set()
        items = []

        def add(it):
            dedup = it['title'].lower().replace(' ', '')
            if dedup in seen:
                return
            seen.add(dedup)
            items.append(it)

        # 港/美块：先取 7x24 快讯中标题命中关键词的，避免 A 股新闻串块
        if key in kw:
            for it in fast:
                if kw[key].search(it['_body']):
                    add(it)
        if len(items) < caps[key]:
            for c in cols:
                try:
                    for it in _em_news_column(c, 20 if key == 'a' else 12):
                        if key in kw and not kw[key].search(it['title']):
                            continue
                        add(it)
                except Exception as e:
                    print(f'[news] column {c} failed: {e}', flush=True)
        items.sort(key=lambda x: x['pubDate'], reverse=True)
        out[key] = [{kk: v for kk, v in it.items() if kk != '_body'}
                    for it in items[:caps[key]]]
    _em_news_cache['ts'], _em_news_cache['data'] = now, out
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = 'vibecode-stockreports/1.0'

    def _send(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Access-Control-Allow-Origin', '*')
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
            if path == '/api/news/data':
                # 股市日报（stock-daily.html）的东财快讯代理，读接口无需口令
                self._send({'ok': True, 'blocks': em_news_blocks()})

            elif path == '/api/reports/health':
                self._send({'ok': True, 'service': 'vibecode-stockreports', 'ts': time.time()})

            elif path == '/api/reports/list':
                self._send(reports_list())

            elif path == '/api/reports/generate':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                code = str(data.get('code', '')).strip()
                market = str(data.get('market', 'A')).strip() or 'A'
                self._send(reports_generate(code, market))

            elif path == '/api/reports/delete':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                self._send(reports_delete(data.get('filename', '')))

            elif path == '/api/reports/pdf':
                data = json.loads(self._read_body())
                if not self._token_ok(data):
                    return
                self._send(reports_pdf(data.get('filename', '')))

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
    httpd = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    httpd.daemon_threads = True
    print('vibecode-stockreports listening 127.0.0.1:%d' % PORT, flush=True)
    httpd.serve_forever()
