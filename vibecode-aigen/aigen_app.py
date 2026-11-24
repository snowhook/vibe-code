#!/usr/bin/env python3
"""vibecode-aigen 独立服务 — AI人物生成平台数据后端 + 生图同源代理 + AI接口工具箱
（2026-11-25 从 server.py 单体拆分；逐条复刻 /api/aigen/*、/api/proxy、/api/aikit/*）
监听 127.0.0.1:5106，nginx 前缀分流，前端 /aigen/ 页面零改动。
数据：本目录 aigen.db（拆分时从 workspace 一致性复制）；图片仍在
/usr/share/nginx/html/{footprint-images,pose-out}/（nginx 直接托管，不迁移）。
"""
import gzip
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)

import aigen_store

TOKEN_FILE = '/home/admin/.openclaw/workspace/.secrets/footprint_token'
PORT = 5106


def write_token():
    """口令来源同主服务：环境变量 FOOTPRINT_TOKEN 优先，其次共享口令文件。
    读不到返回空串——受保护写一律拒绝（fail closed）。"""
    env = os.environ.get('FOOTPRINT_TOKEN', '').strip()
    if env:
        return env
    try:
        return open(TOKEN_FILE, encoding='utf-8').read().strip()
    except OSError:
        return ''


class Handler(BaseHTTPRequestHandler):
    timeout = 30

    # ── 基础收发 ──────────────────────────────────────────────────────────
    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(length).decode('utf-8') if length else ''

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        # 2026-11-13 行为保持：大 JSON（如「我的图库」2.7MB 清单）走 gzip；<2KB 不压
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

    def _require_token(self, data, label='protected'):
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

    # ── /api/aigen/* 统一数据后端（aigen_store.dispatch；写要口令，读不要）──
    def _handle_aigen(self, method):
        parsed = urlparse(self.path)
        subpath = parsed.path[len('/api/aigen'):]
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        body = {}
        if method != 'GET':
            raw = self._read_body()
            if raw:
                try:
                    body = json.loads(raw)
                except Exception:
                    self.send_json({'ok': False, 'error': 'bad json'}, 400)
                    return
            if not self._require_token(body, 'aigen'):
                return
        try:
            status, payload = aigen_store.dispatch(method, subpath, query, body)
            self.send_json(payload, status)
        except Exception as e:
            self.send_json({'ok': False, 'error': str(e)}, 500)

    # ── /api/proxy 生图同源代理（aigen 前端用，白名单转发）────────────────
    # 上游白名单（含 2026-11-24 百炼业务空间后缀规则），与主服务一字不差。
    POSE_PROXY_ALLOWED_HOSTS = (
        'dashscope.aliyuncs.com',
        'maas.qianwenaiapi.com',
        'token-plan.cn-beijing.maas.aliyuncs.com',
        'ark.cn-beijing.volces.com',
        'api.minimaxi.com',
        'api.minimax.io',
        'generativelanguage.googleapis.com',
        'api.openai.com',
        'api-beijing.klingai.com',
        'api.deepseek.com',
    )
    POSE_PROXY_ALLOWED_SUFFIXES = ('.maas.aliyuncs.com', '.dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com', '.qianwenaiapi.com')

    @classmethod
    def _host_allowed(cls, host):
        h = (host or '').lower()
        if not h:
            return False
        if h in cls.POSE_PROXY_ALLOWED_HOSTS:
            return True
        return any(h.endswith(s) for s in cls.POSE_PROXY_ALLOWED_SUFFIXES)

    # token-plan 生图服务端代持 key（浏览器零暴露）：客户端不带 Authorization 时注入
    QWEN_INJECT_HOSTS = ('token-plan.cn-beijing.maas.aliyuncs.com',)
    QWEN_CFG_PATH = os.path.expanduser('~/.openclaw/openclaw.json')
    _qwen_key_cache = (0.0, '')

    @classmethod
    def _qwen_image_key(cls):
        """读 token-plan apiKey（按 mtime 缓存）。失败返回空串；任何日志/响应绝不带出 key。"""
        try:
            mtime = os.path.getmtime(cls.QWEN_CFG_PATH)
            if cls._qwen_key_cache[0] != mtime:
                with open(cls.QWEN_CFG_PATH, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                prov = (cfg.get('models') or {}).get('providers') or {}
                key = str((prov.get('qwenTokenPersonalProvider') or {}).get('apiKey') or '').strip()
                if key.lower() in ('none', ''):
                    key = ''
                cls._qwen_key_cache = (mtime, key)
        except Exception:
            return ''
        return cls._qwen_key_cache[1]

    def _handle_pose_proxy(self):
        target = (self.headers.get('x-target-url') or '').strip()
        try:
            u = urlparse(target)
            allowed = u.scheme == 'https' and self._host_allowed(u.hostname)
        except ValueError:
            u = None
            allowed = False
        if not allowed:
            try:
                print('[pose-proxy] reject host=%r' % (getattr(u, 'hostname', None),), flush=True)
            except Exception:
                pass
            self.send_json({'error': {'message': 'Target origin not allowed'}}, 403)
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        # 2026-11-24: 豆包/可灵 base64 参考图可超 16MiB；40MiB 内由本服务回可读 JSON
        if length > 40 * 1024 * 1024:
            self.send_json({'error': {'message': 'Request body exceeds 40 MiB（参考图太多/太大）'}}, 413)
            return
        raw = self.rfile.read(length) if length > 0 else None
        # 2026-11-13: 方舟 b64_json 大响应跨境易断 → 改写为 url 响应（客户端两种都支持）
        if u.hostname == 'ark.cn-beijing.volces.com' and raw:
            try:
                _b = json.loads(raw.decode('utf-8'))
                if isinstance(_b, dict) and _b.get('response_format') == 'b64_json':
                    _b['response_format'] = 'url'
                    raw = json.dumps(_b, ensure_ascii=False).encode('utf-8')
                    print('[pose-proxy] rewrite response_format b64_json -> url', flush=True)
            except Exception:
                pass
        # 诊断日志（不含密钥；图片只记有无）
        try:
            if raw and len(raw) < 400000:
                _j = json.loads(raw.decode('utf-8', 'replace'))
                print('[pose-req] %s%s model=%r size=%r rf=%r wm=%r seq=%r img=%s prompt=%r' % (
                    u.hostname, u.path, _j.get('model'), _j.get('size'),
                    _j.get('response_format'), _j.get('watermark'),
                    _j.get('sequential_image_generation'),
                    'yes' if _j.get('image') else 'no',
                    (_j.get('prompt') or '')[:40]), flush=True)
        except Exception:
            pass
        fwd_headers = {}
        for h in ('content-type', 'authorization', 'x-goog-api-key', 'x-dashscope-async'):
            v = self.headers.get(h)
            if v:
                fwd_headers[h] = v
        # 服务端代持注入：token-plan 生图 + 客户端没带真 Authorization + 来源是本站
        auth = (fwd_headers.get('authorization') or '').strip()
        referer = (self.headers.get('referer') or self.headers.get('origin') or '').strip()
        if (u.hostname in self.QWEN_INJECT_HOSTS
                and u.path.startswith('/api/v1/services/aigc/')
                and auth.lower() in ('', 'bearer', 'bearer server')
                and referer.startswith(('https://gr.czyuma.com', 'http://127.0.0.1', 'http://localhost'))):
            key = self._qwen_image_key()
            if key:
                fwd_headers['authorization'] = 'Bearer ' + key
        # 排查用指纹日志（不含密钥明文）
        try:
            _auth = (fwd_headers.get('authorization') or '').strip()
            _host = (u.hostname or '')
            if _auth and (_host.endswith('.aliyuncs.com') or _host.endswith('qianwenaiapi.com')):
                _tok = _auth.split(' ', 1)[1] if ' ' in _auth else ''
                print('[pose-proxy] auth host=%s bearer=%s len=%d sha1=%s sk=%s' % (
                    _host, 'yes' if _auth.lower().startswith('bearer ') else 'no', len(_tok),
                    hashlib.sha1(_tok.encode('utf-8', 'replace')).hexdigest()[:8],
                    'yes' if _tok.startswith('sk-') else 'no'), flush=True)
        except Exception:
            pass
        # 2026-11-13: >200KB 的 TLS 请求体 python(urllib) 发北京常 SSLEOFError，curl 稳定
        body_path = out_path = hdr_path = None
        try:
            fd, body_path = tempfile.mkstemp(prefix='pose-body-')
            os.close(fd)
            if raw:
                with open(body_path, 'wb') as _bf:
                    _bf.write(raw)
            fd, out_path = tempfile.mkstemp(prefix='pose-resp-')
            os.close(fd)
            fd, hdr_path = tempfile.mkstemp(prefix='pose-hdr-')
            os.close(fd)
            cmd = ['curl', '-sS', '--max-time', '840', '-o', out_path, '-D', hdr_path,
                   '-w', '%{http_code}', '-X', (self.command or 'POST').upper(), target]
            for _k, _v in fwd_headers.items():
                cmd += ['-H', '%s: %s' % (_k, _v)]
            if raw:
                cmd += ['--data-binary', '@' + body_path]
            try:
                # 子进程上限须高于 curl --max-time，否则 Python 先杀进程
                _p = subprocess.run(cmd, capture_output=True, timeout=860)
            except subprocess.TimeoutExpired:
                # 不回显 cmd（内含 Authorization Key）
                self.send_json({'error': {'message': '上游生成超时（超过 840 秒）。三视图/大图耗时较长，可重试、或改用更快模型/降低分辨率。'}}, 504)
                return
            try:
                status = int((_p.stdout or b'0').decode('utf-8', 'replace').strip() or 0)
            except ValueError:
                status = 0
            with open(out_path, 'rb') as _rf:
                resp_body = _rf.read()
            ctype = 'application/json; charset=utf-8'
            with open(hdr_path, 'r', errors='replace') as _hf:
                for _line in _hf:
                    if _line.lower().startswith('content-type:'):
                        ctype = _line.split(':', 1)[1].strip()
                        break
            if status == 0:
                raise RuntimeError((_p.stderr or b'').decode('utf-8', 'replace').strip()[:300] or 'curl failed')
        except Exception as e:
            self.send_json({'error': {'message': f'Proxy request failed: {e}'}}, 502)
            return
        finally:
            for _t in (body_path, out_path, hdr_path):
                try:
                    if _t:
                        os.unlink(_t)
                except Exception:
                    pass
        try:
            print('[pose-proxy] %s%s -> %s %r' % (u.hostname, u.path, status, resp_body[:300]), flush=True)
        except Exception:
            pass
        # 方舟成品图抓回 /pose-out/ 换同源地址（TOS 无 CORS、跨境慢）
        if (u.hostname == 'ark.cn-beijing.volces.com' and status == 200
                and resp_body and b'"url"' in resp_body):
            try:
                _j = json.loads(resp_body.decode('utf-8'))
                _arr = _j.get('data') or []
                _first = _arr[0] if _arr and isinstance(_arr[0], dict) else None
                _src = _first.get('url') if _first else None
                if _src and str(_src).startswith('https://'):
                    _req = urllib.request.Request(str(_src), headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(_req, timeout=90) as _r:
                        _img = _r.read()
                    _sum = hashlib.sha1(_img).hexdigest()[:16]
                    _dir = '/usr/share/nginx/html/pose-out'
                    os.makedirs(_dir, exist_ok=True)
                    _name = 'ark_%s.png' % _sum
                    _p = os.path.join(_dir, _name)
                    with open(_p, 'wb') as _f:
                        _f.write(_img)
                    try:
                        os.chmod(_p, 0o644)
                    except Exception:
                        pass
                    _first['url'] = '/pose-out/' + _name
                    resp_body = json.dumps(_j, ensure_ascii=False).encode('utf-8')
                    print('[pose-proxy] ark image saved -> %s (%d bytes)' % (_first['url'], len(_img)), flush=True)
            except Exception as _e:
                print('[pose-proxy] ark image capture failed: %r' % (_e,), flush=True)
        # 千问/百炼 dashscope OSS 跨域 url 抓回（choices[].content[].image 与 results[].url 两形态）
        if ((u.hostname in ('token-plan.cn-beijing.maas.aliyuncs.com', 'dashscope.aliyuncs.com', 'maas.qianwenaiapi.com')
                or (u.hostname or '').endswith('.maas.aliyuncs.com')
                or (u.hostname or '').endswith('.qianwenaiapi.com')) and status == 200
                and resp_body and b'oss' in resp_body and (b'"image"' in resp_body or b'"url"' in resp_body)):
            try:
                _j = json.loads(resp_body.decode('utf-8'))
                _out = _j.get('output') or {}
                _urls = []
                for _ch in (_out.get('choices') or []):
                    for _part in ((_ch.get('message') or {}).get('content') or []):
                        if isinstance(_part, dict) and isinstance(_part.get('image'), str) and _part['image'].startswith('https://'):
                            _urls.append(('choice', _part))
                for _r in (_out.get('results') or []):
                    if isinstance(_r, dict) and isinstance(_r.get('url'), str) and _r['url'].startswith('https://'):
                        _urls.append(('result', _r))
                _dir = '/usr/share/nginx/html/pose-out'
                os.makedirs(_dir, exist_ok=True)
                for _kind, _obj in _urls:
                    _src = _obj.get('image') if _kind == 'choice' else _obj.get('url')
                    _req = urllib.request.Request(str(_src), headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(_req, timeout=90) as _r2:
                        _img = _r2.read()
                    _sum = hashlib.sha1(_img).hexdigest()[:16]
                    _name = 'qwen_%s.png' % _sum
                    _fp = os.path.join(_dir, _name)
                    with open(_fp, 'wb') as _f2:
                        _f2.write(_img)
                    try:
                        os.chmod(_fp, 0o644)
                    except Exception:
                        pass
                    if _kind == 'choice':
                        _obj['image'] = '/pose-out/' + _name
                    else:
                        _obj['url'] = '/pose-out/' + _name
                    print('[pose-proxy] qwen image saved -> /pose-out/%s (%d bytes)' % (_name, len(_img)), flush=True)
                if _urls:
                    resp_body = json.dumps(_j, ensure_ascii=False).encode('utf-8')
            except Exception as _e:
                print('[pose-proxy] qwen image capture failed: %r' % (_e,), flush=True)
        # 可灵结果图跨境 CDN 无 CORS 抓回；兼容旧版 data.task_result.images 与 4.0 data[].outputs
        if (u.hostname == 'api-beijing.klingai.com' and status == 200
                and resp_body and (b'task_result' in resp_body or b'"outputs"' in resp_body)):
            try:
                _j = json.loads(resp_body.decode('utf-8'))
                _data = _j.get('data')
                _first_img = None
                if isinstance(_data, list):
                    for _task in _data:
                        if not isinstance(_task, dict):
                            continue
                        for _o in (_task.get('outputs') or []):
                            if isinstance(_o, dict) and _o.get('type') == 'image' and _o.get('url'):
                                _first_img = _o
                                break
                        if _first_img:
                            break
                elif isinstance(_data, dict):
                    _imgs = (_data.get('task_result') or {}).get('images') or []
                    _first_img = _imgs[0] if _imgs and isinstance(_imgs[0], dict) else None
                _src = _first_img.get('url') if _first_img else None
                if _src and str(_src).startswith('https://'):
                    _dir = '/usr/share/nginx/html/pose-out'
                    os.makedirs(_dir, exist_ok=True)
                    _req = urllib.request.Request(str(_src), headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(_req, timeout=120) as _r3:
                        _img = _r3.read()
                    _sum = hashlib.sha1(_img).hexdigest()[:16]
                    _name = 'kling_%s.png' % _sum
                    _fp = os.path.join(_dir, _name)
                    with open(_fp, 'wb') as _f2:
                        _f2.write(_img)
                    try:
                        os.chmod(_fp, 0o644)
                    except Exception:
                        pass
                    _first_img['url'] = '/pose-out/' + _name
                    resp_body = json.dumps(_j, ensure_ascii=False).encode('utf-8')
                    print('[pose-proxy] kling image saved -> /pose-out/%s (%d bytes)' % (_name, len(_img)), flush=True)
            except Exception as _e:
                print('[pose-proxy] kling image capture failed: %r' % (_e,), flush=True)

        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(resp_body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PATCH, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers',
                         'Content-Type, Authorization, X-Target-Url, X-Goog-Api-Key, X-Dashscope-Async, X-Footprint-Token')
        self.send_header('Access-Control-Expose-Headers', '*')
        self.end_headers()
        self.wfile.write(resp_body)

    # ── /api/aikit/* AI 接口工具箱 ─────────────────────────────────────────
    #   * 只允许出站到白名单主机（防开放代理）
    #   * 密钥只在请求体里，不写日志、不回显
    #   * apiKey='server' = 服务器代持 key（目前仅通义 token-plan）
    AIKIT_PROVIDERS = {
        'ark': {
            'label': '豆包 / 火山方舟',
            'baseUrl': 'https://ark.cn-beijing.volces.com',
            'listPaths': ['/api/v3/models?PageSize=100', '/api/v3/endpoints?PageSize=100'],
            'probePath': '/api/v3/images/generations',
        },
        'openai': {
            'label': 'OpenAI',
            'baseUrl': 'https://api.openai.com',
            'listPaths': ['/v1/models'],
            'probePath': '/v1/images/generations',
        },
        'gemini': {
            'label': 'Gemini',
            'baseUrl': 'https://generativelanguage.googleapis.com',
            'listPaths': ['/v1beta/models?key={KEY}'],
            'probePath': None,
        },
        'deepseek': {
            'label': 'DeepSeek',
            'baseUrl': 'https://api.deepseek.com',
            'listPaths': ['/models', '/v1/models'],
            'probePath': '/chat/completions',
            'probeBody': {'model': '{MODEL}', 'messages': [], 'max_tokens': 1},
        },
        'qwen': {
            'label': '通义 / 阿里云百炼',
            'baseUrl': 'https://dashscope.aliyuncs.com',
            'listPaths': ['/compatible-mode/v1/models', '/api/v1/models'],
            'probePath': None,
        },
    }
    AIKIT_RATE = (30, 60.0)  # 每 IP 60 秒最多 30 次
    _aikit_hits = {}

    @classmethod
    def _aikit_throttle(cls, ip):
        now = time.time()
        window, span = cls.AIKIT_RATE[0], cls.AIKIT_RATE[1]
        hits = [t for t in cls._aikit_hits.get(ip, []) if now - t < span]
        if len(hits) >= window:
            cls._aikit_hits[ip] = hits
            return False
        hits.append(now)
        cls._aikit_hits[ip] = hits
        return True

    @staticmethod
    def _aikit_join(base, path):
        """base 已含版本段（/api/v3、/v1）时不重复拼接。"""
        seg = ''
        m = re.match(r'^/([^/?]+)', path)
        if m:
            seg = m.group(1)
        if seg and base.endswith('/' + seg):
            return base + path[len(seg) + 1:]
        return base + path

    @classmethod
    def _aikit_extract(cls, node, out=None, depth=0):
        """从各家返回里抠模型名：data[] / items[] / Result.Items[] / models[] / endpoint 列表。"""
        if out is None:
            out = []
        if depth > 5 or node is None:
            return out
        if isinstance(node, list):
            for it in node:
                if isinstance(it, str):
                    cls._aikit_push(out, it)
                elif isinstance(it, dict):
                    mid = it.get('id') or it.get('Id') or it.get('model') or it.get('ModelID') or it.get('value')
                    label = ''
                    ref = it.get('ModelReference') if isinstance(it.get('ModelReference'), dict) else None
                    if ref:
                        fm = ref.get('FoundationModel') if isinstance(ref.get('FoundationModel'), dict) else None
                        if fm:
                            label = str(fm.get('ModelID') or '')
                    name = it.get('name') or it.get('Name')
                    if mid:
                        cls._aikit_push(out, str(mid), label or (name if name and name != mid else ''))
                    elif name:
                        cls._aikit_push(out, str(name))
                    else:
                        cls._aikit_extract(it, out, depth + 1)
                    if label and not mid:
                        cls._aikit_push(out, label)
            return out
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and re.search(r'model|^id$|name', k, re.I) and re.search(r'[A-Za-z0-9._-]{3,}', v):
                    if not re.search(r'^(https?|application/json)', v, re.I):
                        cls._aikit_push(out, v)
                elif isinstance(v, (dict, list)):
                    cls._aikit_extract(v, out, depth + 1)
        return out

    @staticmethod
    def _aikit_push(out, mid, label=''):
        mid = str(mid).strip()
        if not mid or len(mid) > 120:
            return
        for it in out:
            if it['id'] == mid:
                if label and not it.get('label'):
                    it['label'] = str(label)
                return
        out.append({'id': mid, 'label': str(label or '')})

    def _aikit_call(self, url, key, body=None, timeout=15):
        headers = {}
        if key and key not in ('server',):
            headers['Authorization'] = 'Bearer ' + key
        data = None
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, headers=headers, method='POST' if data else 'GET')
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()[:200000]
        except urllib.error.HTTPError as e:
            try:
                raw = e.read()[:4000]
            except Exception:
                raw = b''
            return e.code, raw
        except Exception as e:
            return 0, json.dumps({'error': {'message': str(e)}}).encode('utf-8')

    def _handle_aikit(self, want):
        ip = (self.headers.get('x-forwarded-for') or self.client_address[0] or '').split(',')[0].strip()
        if not self._aikit_throttle(ip):
            self.send_json({'ok': False, 'error': 'too many requests'}, 429)
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if length > 1024 * 1024:
            self.send_json({'ok': False, 'error': 'body too large'}, 413)
            return
        try:
            body = json.loads(self.rfile.read(length).decode('utf-8')) if length else {}
        except Exception:
            body = {}
        if want == 'providers':
            self.send_json({'ok': True, 'providers': [
                {'id': pid, 'label': p['label'], 'baseUrl': p['baseUrl'],
                 'listPaths': p['listPaths'], 'probePath': p['probePath'],
                 'serverKey': bool(p.get('serverKey'))}
                for pid, p in self.AIKIT_PROVIDERS.items()]})
            return
        pid = str(body.get('provider') or '').strip().lower()
        preset = self.AIKIT_PROVIDERS.get(pid)
        base = str(body.get('baseUrl') or (preset or {}).get('baseUrl') or '').strip().rstrip('/')
        if not base and not preset:
            self.send_json({'ok': False, 'error': "provider 或 baseUrl 必填（provider 可选：%s）" % ', '.join(self.AIKIT_PROVIDERS)}, 400)
            return
        key = str(body.get('apiKey') or '').strip()
        if key in ('server', '') and preset and preset.get('serverKey'):
            key = self._qwen_image_key()
        need_key = not (preset or {}).get('serverKey')
        if need_key and not key:
            self.send_json({'ok': False, 'error': 'apiKey 必填'}, 400)
            return
        host = urlparse(base).hostname or ''
        allow = {urlparse(p['baseUrl']).hostname for p in self.AIKIT_PROVIDERS.values()}
        allow |= set(self.POSE_PROXY_ALLOWED_HOSTS)
        if host not in allow:
            self.send_json({'ok': False, 'error': 'baseUrl 主机不在白名单：%s' % host,
                            'allowedHosts': sorted(allow)}, 403)
            return
        paths = body.get('listPaths') or (preset or {}).get('listPaths') or []
        probe_path = body.get('probePath') if 'probePath' in body else (preset or {}).get('probePath')
        model = str(body.get('model') or '').strip()
        tries, models, last = [], [], None
        for tpl in paths:
            url = self._aikit_join(base, tpl.replace('{KEY}', urllib.parse.quote(key, safe='')))
            status, raw = self._aikit_call(url, key)
            snippet = raw.decode('utf-8', 'replace')[:400]
            tries.append({'url': url.replace(urllib.parse.quote(key, safe=''), '***') if key else url,
                          'status': status, 'body': snippet[:200]})
            last = (status, snippet)
            if status == 200:
                try:
                    models = self._aikit_extract(json.loads(snippet if len(snippet) < 400 else raw.decode('utf-8', 'replace')))
                except Exception:
                    models = []
                if models:
                    break
        if models:
            self.send_json({'ok': True, 'auth': True, 'via': 'models',
                            'count': len(models), 'models': models, 'tries': tries})
            return
        if want == 'models' and not probe_path:
            self.send_json({'ok': False, 'auth': None, 'via': 'models', 'models': [],
                            'error': '该服务没有可用的模型列表接口，请手动填模型名', 'tries': tries},
                           200)
            return
        if not probe_path:
            self.send_json({'ok': False, 'auth': None, 'via': 'models', 'models': [],
                            'error': '拿不到模型列表，且该服务没有探针接口', 'tries': tries}, 200)
            return
        pbody = {'model': model or 'probe-model', 'prompt': ''}
        preset_body = (preset or {}).get('probeBody')
        if isinstance(preset_body, dict):
            pbody = json.loads(json.dumps(preset_body).replace('{MODEL}', model or 'probe-model'))
        status, raw = self._aikit_call(self._aikit_join(base, probe_path), key, body=pbody)
        snippet = raw.decode('utf-8', 'replace')[:600]
        low = snippet.lower()
        auth_ok = not (status in (401, 403) or 'authentication' in low or 'invalid api key' in low or '格式' in snippet)
        self.send_json({'ok': bool(auth_ok and status in (200, 400, 404, 422)),
                        'auth': auth_ok, 'via': 'probe', 'status': status,
                        'models': [], 'error': None if auth_ok and status in (400, 422) else snippet[:400],
                        'probeBody': snippet[:300], 'tries': tries}, 200)

    # ── 方法分发（与主服务同源：aigen 四法 + proxy POST/GET + aikit 三入口）──
    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == '/api/aigen/health':
                self.send_json({'ok': True, 'service': 'vibecode-aigen', 'ts': time.time()})
            elif path.startswith('/api/aigen/') or path == '/api/aigen':
                self._handle_aigen('POST')
            elif path == '/api/proxy':
                self._handle_pose_proxy()
            elif path == '/api/aikit/providers':
                self._handle_aikit('providers')
            elif path == '/api/aikit/test':
                self._handle_aikit('test')
            elif path == '/api/aikit/models':
                self._handle_aikit('models')
            else:
                self.send_json({'ok': False, 'error': 'not found'}, 404)
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self.send_json({'ok': False, 'error': str(e)}, 500)
            except Exception:
                pass

    def do_PATCH(self):
        if urlparse(self.path).path.startswith('/api/aigen'):
            self._handle_aigen('PATCH')
            return
        self.send_error(405, 'Method Not Allowed')

    def do_PUT(self):
        if urlparse(self.path).path.startswith('/api/aigen'):
            self._handle_aigen('PUT')
            return
        self.send_error(405, 'Method Not Allowed')

    def do_DELETE(self):
        if urlparse(self.path).path.startswith('/api/aigen'):
            self._handle_aigen('DELETE')
            return
        self.send_error(405, 'Method Not Allowed')

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/aigen/health':
            self.send_json({'ok': True, 'service': 'vibecode-aigen', 'ts': time.time()})
            return
        if parsed.path.startswith('/api/aigen'):
            self._handle_aigen('GET')
            return
        if parsed.path == '/api/proxy':
            self._handle_pose_proxy()
            return
        self.send_error(404, 'Not found')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PATCH, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Target-Url, X-Goog-Api-Key, X-Dashscope-Async, X-Footprint-Token')
        self.end_headers()

    def log_message(self, fmt, *args):
        print('[%s] %s' % (time.strftime('%F %T'), fmt % args), flush=True)


if __name__ == '__main__':
    aigen_store.init()
    print('Initialized aigen store: %s' % aigen_store.AIGEN_DB, flush=True)
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    httpd.daemon_threads = True
    print('vibecode-aigen listening 127.0.0.1:%d' % PORT, flush=True)
    httpd.serve_forever()
