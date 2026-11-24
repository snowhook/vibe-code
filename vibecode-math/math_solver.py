#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动画化解题 — 后端引擎
流程：图片识别(VL) → 详细解答(LLM+SVG) → HyperFrames 解说动画渲染 → 历史档案
存储：文件型（math_videos/index.json + math_videos/<id>/record.json），不碰 routing.db 避免锁冲突。
LLM 配置读自 ~/.openclaw/openclaw.json 的 models.providers（姿势同 bazi_llm.py）。
"""
import os
import re
import json
import time
import uuid
import base64
import shutil
import threading
import subprocess
import urllib.request
import urllib.error

CONFIG_PATH = os.path.expanduser('~/.openclaw/openclaw.json')
PROVIDER = 'qwenTokenPersonalProvider'
TEXT_MODEL = 'qwen3.8-max-preview'   # 解题（质量优先）
VL_MODEL = 'qwen3.7-plus'            # 看图解题/重读图（推理质量优先）
OCR_MODEL = 'qwen3.6-flash'          # 纯 OCR 识别（实测 2.3s vs plus 6.2s，准确率相当）
TIMEOUT = 240

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'math_videos')
NGINX_VIDEO_DIR = '/usr/share/nginx/html/math-videos'
NPX = os.path.expanduser('~/.nvm/versions/node/v24.21.0/bin/npx')
HF_VERSION = '0.8.64'          # 与实测通过的版本一致，保证渲染可复现
RENDER_TIMEOUT = 2400          # 渲染最长 40 分钟
MAX_IMAGE_BYTES = 10 * 1024 * 1024

# ── TTS 配音 ──
SECRETS_DIR = os.path.expanduser('~/.openclaw/workspace/.secrets')
FFPROBE = '/usr/local/bin/ffprobe'
FFMPEG = '/usr/local/bin/ffmpeg'
# Qwen-Audio-3.0-TTS：走 Token Plan 网关的原生 TTS 口（HTTP，无需 SDK/WebSocket）
ALI_TTS_URL = 'https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer'
ALI_TTS_MODEL = 'qwen-audio-3.0-tts-plus'
HEYGEN_TTS_URL = 'https://api.heygen.com/v3/voices/speech'
HEYGEN_DEFAULT_VOICE = 'a804d35f49a84e1d8a40bd10d5c8e4ee'   # 用户在 HeyGen 自建的音色
# 已逐个实测可用（Plus 模型）；其余 longan* 音色在该模型上会返回 411 Engine error
ALI_VOICES = [
    ('longanlingxin', '龙安灵心 · 知心温暖（女声）'),
    ('longanlingxi', '龙安灵希 · 可爱甜美（女声）'),
    ('longanhuan_v3.6', '龙安欢 · 欢脱元气（女声）'),
    ('longanlufeng', '龙安鲁风 · 明亮开朗（男声）'),
]

_index_lock = threading.Lock()
_task_lock = threading.Lock()
_tasks = {}                    # id -> {status, progress, error, video_url}


# ─────────────────────────── LLM 基础 ───────────────────────────

def _get_api_key():
    with open(CONFIG_PATH, encoding='utf-8') as f:
        cfg = json.load(f)
    prov = cfg.get('models', {}).get('providers', {}).get(PROVIDER, {})
    key = prov.get('apiKey', '')
    base = prov.get('baseUrl', '')
    if not key or key.startswith('{') or not base:
        raise RuntimeError('LLM provider 未配置或 apiKey 为 SecretRef（%s）' % PROVIDER)
    return base.rstrip('/') + '/chat/completions', key


def _call_llm(system_prompt, user_content, model=None, temperature=0.4, max_tokens=8000):
    """user_content: str 或 VL 消息 content 列表"""
    url, key = _get_api_key()
    body = json.dumps({
        'model': model or TEXT_MODEL,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_content},
        ],
        'temperature': temperature,
        'max_tokens': max_tokens,
    }, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST', headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bea' + 'rer ' + key,
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    return data['choices'][0]['message']['content']


# ─────────────────────────── TTS 配音 ───────────────────────────

def _ali_tts_key():
    """TTS 用 key：优先取 Token Plan provider（与解题 LLM 同一把），
    回退旧 .secrets/ALI_TTS_API_KEY（DashScope 公网 key，只对旧通道有效）。"""
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            cfg = json.load(f)
        prov = (cfg.get('models') or {}).get('providers', {}).get(PROVIDER, {}) or {}
        k = prov.get('apiKey', '')
        if isinstance(k, str) and k and not k.startswith('{'):
            return k
    except (OSError, ValueError, AttributeError):
        pass
    p = os.path.join(SECRETS_DIR, 'ALI_TTS_API_KEY')
    try:
        with open(p, encoding='utf-8') as f:
            k = f.read().strip()
        return k or None
    except OSError:
        return None


def _heygen_api_key():
    """HeyGen API key（~/.heygen/credentials 由 heygen CLI 维护，0600 权限）"""
    p = os.path.expanduser('~/.heygen/credentials')
    try:
        with open(p, encoding='utf-8') as f:
            cred = json.load(f)
        return cred.get('api_key') or None
    except (OSError, ValueError):
        return None


def voice_options():
    """前端配音选项：无配音 + 阿里 AI 音色（有 key 时）+ HeyGen 自建音色（有凭据时）"""
    opts = [{'kind': 'none', 'voice': '', 'label': '🔇 无配音（纯字幕）'}]
    if _ali_tts_key():
        for v, lbl in ALI_VOICES:
            opts.append({'kind': 'ali', 'voice': v, 'label': '🤖 阿里AI · ' + lbl})
    if _heygen_api_key():
        opts.append({'kind': 'heygen', 'voice': HEYGEN_DEFAULT_VOICE, 'label': '🎤 HeyGen · 我的音色'})
    return {'ok': True, 'options': opts}


def _download(url, outp, timeout=90):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    with open(outp, 'wb') as f:
        f.write(data)
    return len(data)


def _tts_ali(text, voice, out_wav):
    key = _ali_tts_key()
    if not key:
        raise RuntimeError('未配置 TTS API Key（Token Plan provider / ALI_TTS_API_KEY）')
    body = json.dumps({'model': ALI_TTS_MODEL,
                       'input': {'text': text, 'voice': voice,
                                 'format': 'wav', 'sample_rate': 24000}},
                      ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(ALI_TTS_URL, data=body, method='POST', headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bea' + 'rer ' + key})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            obj = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'ignore')[:300]
        raise RuntimeError('TTS 合成失败 HTTP %s: %s' % (e.code, detail))
    url = ((obj.get('output') or {}).get('audio') or {}).get('url')
    if not url:
        raise RuntimeError('TTS 无音频返回: %s' % json.dumps(obj, ensure_ascii=False)[:200])
    _download(url, out_wav)
    return out_wav


def _tts_heygen(text, voice_id, out_wav):
    key = _heygen_api_key()
    if not key:
        raise RuntimeError('未配置 HeyGen 凭据（heygen auth login）')
    body = json.dumps({'text': text, 'voice_id': voice_id}, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(HEYGEN_TTS_URL, data=body, method='POST', headers={
        'Content-Type': 'application/json',
        'X-Api-Key': key})
    with urllib.request.urlopen(req, timeout=120) as resp:
        obj = json.loads(resp.read().decode('utf-8'))
    inner = obj.get('data') or {}
    url = inner.get('audio_url')
    if not url:
        raise RuntimeError('HeyGen TTS 无 audio_url: %s' % json.dumps(obj, ensure_ascii=False)[:200])
    mp3 = out_wav + '.mp3'
    _download(url, mp3)
    rc = subprocess.run([FFMPEG, '-y', '-i', mp3, '-ar', '44100', '-ac', '1', out_wav],
                        capture_output=True, timeout=120)
    try:
        os.remove(mp3)
    except OSError:
        pass
    if rc.returncode != 0 or not os.path.exists(out_wav):
        raise RuntimeError('HeyGen 音频转码失败')
    return out_wav


def _wav_duration(path):
    try:
        out = subprocess.run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration',
                              '-of', 'default=nw=1:nk=1', path],
                             capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return None


def synthesize_voiceover(proj, scenes, voice):
    """逐场景合成配音 → proj/assets/voice/n*.wav；返回与 scenes 等长列表（无解说词为 None）"""
    kind = (voice or {}).get('kind')
    vname = (voice or {}).get('voice') or ''
    if kind not in ('ali', 'heygen') or not vname:
        return [None] * len(scenes)
    vdir = os.path.join(proj, 'assets', 'voice')
    os.makedirs(vdir, exist_ok=True)
    out = []
    for i, sc in enumerate(scenes):
        text = re.sub(r'\s+', ' ', str(sc.get('narration') or '')).strip()
        if not text:
            out.append(None)
            continue
        p = os.path.join(vdir, 'n%d.wav' % i)
        if kind == 'heygen':
            _tts_heygen(text[:600], vname, p)
        else:
            _tts_ali(text[:600], vname, p)
        dur = _wav_duration(p)
        if not dur or dur < 0.5:
            raise RuntimeError('场景%d配音时长异常' % (i + 1))
        out.append({'file': 'assets/voice/n%d.wav' % i, 'dur': round(dur, 2)})
    return out


def _extract_json(text):
    """稳健提取 LLM 回复中的 JSON（剥围栏 + 括号深度扫描）"""
    t = (text or '').strip()
    if t.startswith('```'):
        t = re.sub(r'^```\w*\n?', '', t)
        t = re.sub(r'\n?```$', '', t).strip()
    start = t.find('{')
    if start < 0:
        raise ValueError('LLM 回复中未找到 JSON')
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(t[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i + 1])
    raise ValueError('LLM 回复 JSON 不完整')


# ─────────────────────────── 存储 ───────────────────────────

def _ensure_dirs():
    os.makedirs(BASE_DIR, exist_ok=True)
    os.makedirs(NGINX_VIDEO_DIR, exist_ok=True)


def _index_path():
    return os.path.join(BASE_DIR, 'index.json')


def _rec_dir(rid):
    return os.path.join(BASE_DIR, rid)


def _load_index():
    try:
        with open(_index_path(), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def _save_index(items):
    tmp = _index_path() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _index_path())


def _load_record(rid):
    p = os.path.join(_rec_dir(rid), 'record.json')
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def _save_record(rec):
    d = _rec_dir(rec['id'])
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, 'record.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(d, 'record.json'))


def _new_id():
    return 'ms-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:4]


# ─────────────────────────── 净化 ───────────────────────────

def sanitize_svg(svg):
    """去除 SVG 中的脚本与事件属性，防存储型 XSS（页面与视频合成都会内联它）"""
    if not svg:
        return ''
    s = re.sub(r'<script[\s\S]*?</script>', '', svg, flags=re.I)
    s = re.sub(r'<foreignObject[\s\S]*?</foreignObject>', '', s, flags=re.I)
    s = re.sub(r'\son\w+\s*=\s*"[^"]*"', '', s, flags=re.I)
    s = re.sub(r"\son\w+\s*=\s*'[^']*'", '', s, flags=re.I)
    s = re.sub(r'javascript\s*:', '', s, flags=re.I)
    return s.strip()


def esc_html(s):
    return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


# ─────────────────────────── 识别与解题 ───────────────────────────

RECOGNIZE_SYS = '你是题目识别引擎。用户给你一张中学（初中/高中）题目图片，你要把题目完整准确地转写成文本。只输出严格 JSON，不要代码围栏，不要任何解释。'

RECOGNIZE_USER = ('识别图片中的题目，输出 JSON：\n'
                  '{"problem": "题目完整文本，保留所有小问；数学公式用 LaTeX，行内 \\\\(...\\\\)，独立 \\\\[...\\\\]",\n'
                  ' "subject": "数学|物理|化学|生物|语文|英语|其他"}\n'
                  '注意：图中如有几何图形/函数图象，用文字描述其关键要素（点、线、角、坐标、标注值），并入 problem 文本。')


def recognize_image(data_url):
    if not data_url or not data_url.startswith('data:'):
        return {'ok': False, 'error': '缺少图片数据'}
    if len(data_url) > MAX_IMAGE_BYTES * 2:
        return {'ok': False, 'error': '图片过大（>10MB）'}
    content = [
        {'type': 'image_url', 'image_url': {'url': data_url}},
        {'type': 'text', 'text': RECOGNIZE_USER},
    ]
    raw = _call_llm(RECOGNIZE_SYS, content, model=OCR_MODEL, temperature=0.1, max_tokens=3000)
    obj = _extract_json(raw)
    return {'ok': True, 'problem': obj.get('problem', '').strip(), 'subject': obj.get('subject', '其他')}


SOLVE_SYS = ('你是一位顶尖中学教师（覆盖数学/物理/化学/生物等），以讲解清晰、步骤详尽著称。'
             '对用户给出的题目产出详细解答。只输出严格 JSON，不要代码围栏。')

SOLVE_USER_TMPL = '''解下面这道题，输出 JSON：
{{
 "title": "不超过20字的题目短标题",
 "subject": "学科",
 "answer": "最终答案（简洁明确）",
 "summary": "一句话解题思路（用什么方法、为什么）",
 "knowledge": ["涉及知识点", "……"],
 "steps": [
   {{"heading": "第N步·动作短语", "detail": "该步的详细讲解：为什么这么做、怎么做、中间结果。LaTeX 行内公式用 \\\\(...\\\\)。可以长，讲透为止。"}}
 ],
 "svgs": [
   {{"caption": "图的说明", "code": "<svg xmlns=\\"http://www.w3.org/2000/svg\\" viewBox=\\"0 0 800 500\\">...</svg>"}}
 ]
}}
硬性要求：
1. steps 至少 3 步，逐步推进，每步 detail 不少于 50 字，把推理链完整暴露；
2. 如题目涉及几何图形、函数图象、受力分析、电路、光路、化学反应装置等，必须在 svgs 中画出示意图（1~3 张）：viewBox 宽 ≤800，中文标注（font-family 用 "Noto Sans CJK SC"），关键元素用颜色区分，线条清晰，标注数值/点名；纯代数/文字题则 svgs 给空数组；
3. SVG 中禁止 <script>、禁止事件属性；文字用 <text>，字号 ≥16；
4. 所有文本字段中不要出现未转义的双引号破坏 JSON。

题目：
{problem}'''


def _clean_solution(sol):
    """清洗 LLM 产出的解答 JSON → (steps, svgs) 或 None"""
    svgs = []
    for s in (sol.get('svgs') or [])[:3]:
        code = sanitize_svg(s.get('code', ''))
        if code.startswith('<svg'):
            svgs.append({'caption': s.get('caption', ''), 'code': code})
    steps = [{'heading': str(st.get('heading', '')).strip()[:60],
              'detail': str(st.get('detail', '')).strip()}
             for st in (sol.get('steps') or []) if str(st.get('detail', '')).strip()][:12]
    if not steps:
        return None
    return steps, svgs


def solve_problem(problem_text, subject=None, image_data_url=None, note=None):
    problem_text = (problem_text or '').strip()
    has_img = bool(image_data_url and image_data_url.startswith('data:'))
    if not problem_text and not has_img:
        return {'ok': False, 'error': '题目为空'}
    note = (note or '').strip()
    prompt = SOLVE_USER_TMPL.format(problem=(problem_text or '（按图片与备注解题）')[:6000])
    sys_prompt = SOLVE_SYS
    if has_img:
        # 图片题：直接让 VL 模型看图解题（比先 OCR 再解题少一层失真），OCR 文本与备注一并提供
        sys_prompt = (SOLVE_SYS + '用户会提供题目图片，文本部分可能附已识别的题目文本和用户备注。'
                      '以图片为准，按备注要求作答（如指定解某一小题）。只输出严格 JSON。')
        # 关键：图片分支必须带上完整 JSON schema，否则模型自由发挥导致 steps 为空
        # 用 format() 而非 split()，避免模板里的 {{ }} 转义未被还原
        ocr_part = ('【已识别题目文本（可能有误，以图为准）】\n%s\n\n' % problem_text[:5000]) if problem_text else ''
        note_part = ('【用户备注】%s（备注指定了具体小题或特殊要求时，务必遵照）\n\n' % note[:500]) if note else ''
        text = (ocr_part + note_part
                + SOLVE_USER_TMPL.format(problem='（题目在图片中，请仔细读图后作答）'))
        content = [
            {'type': 'image_url', 'image_url': {'url': image_data_url}},
            {'type': 'text', 'text': text},
        ]
        raw = _call_llm(sys_prompt, content, model=VL_MODEL, temperature=0.3, max_tokens=12000)
        # 备注并入存档题干，方便回看
        problem_text = (problem_text or '（图片题）') + ('\n\n[备注] ' + note if note else '')
    else:
        if note:
            prompt += '\n\n【用户备注】%s（务必遵照备注要求作答）' % note[:500]
            problem_text = problem_text + '\n\n[备注] ' + note
        raw = _call_llm(sys_prompt, prompt, temperature=0.3, max_tokens=12000)
    sol = _extract_json(raw)
    cleaned = _clean_solution(sol)
    if not cleaned:
        return {'ok': False, 'error': 'LLM 未产出有效步骤'}
    steps, svgs = cleaned
    rid = _new_id()
    rec = {
        'id': rid,
        'created_at': int(time.time()),
        'updated_at': int(time.time()),
        'subject': subject or sol.get('subject') or '其他',
        'title': str(sol.get('title') or problem_text.strip()[:20] or '图片题')[:40],
        'problem': problem_text.strip(),
        'note': note,
        'has_image': False,
        'solution': {
            'answer': str(sol.get('answer', '')),
            'summary': str(sol.get('summary', '')),
            'knowledge': [str(k)[:30] for k in (sol.get('knowledge') or [])][:8],
            'steps': steps,
            'svgs': svgs,
        },
        'chats': [],
        'animation': {'status': 'none'},
        'exported': False,
        'export_url': None,
    }
    _ensure_dirs()
    d = _rec_dir(rid)
    os.makedirs(d, exist_ok=True)
    if image_data_url and image_data_url.startswith('data:'):
        try:
            m = re.match(r'data:image/(\w+);base64,(.*)', image_data_url, re.S)
            if m and len(m.group(2)) < MAX_IMAGE_BYTES * 2:
                ext = 'jpg' if m.group(1) in ('jpeg', 'jpg') else m.group(1)
                with open(os.path.join(d, 'problem.' + ext), 'wb') as f:
                    f.write(base64.b64decode(m.group(2)))
                rec['has_image'] = True
                rec['image_file'] = 'problem.' + ext
        except Exception:
            pass
    _save_record(rec)
    with _index_lock:
        items = _load_index()
        items.insert(0, {'id': rid, 'created_at': rec['created_at'], 'subject': rec['subject'],
                         'title': rec['title'], 'has_video': False})
        _save_index(items[:500])
    return {'ok': True, 'record': _public_record(rec)}


def _public_record(rec):
    """返回给前端的记录（附带图片相对 URL）"""
    out = json.loads(json.dumps(rec))
    if rec.get('has_image') and rec.get('image_file'):
        out['image_url'] = '/api/math/image?id=%s' % rec['id']
    return out


def get_image_bytes(rid):
    rec = _load_record(rid)
    if not rec or not rec.get('image_file'):
        return None, None
    p = os.path.join(_rec_dir(rid), rec['image_file'])
    if not os.path.exists(p):
        return None, None
    ext = rec['image_file'].rsplit('.', 1)[-1].lower()
    mime = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
            'webp': 'image/webp', 'gif': 'image/gif'}.get(ext, 'image/jpeg')
    with open(p, 'rb') as f:
        return f.read(), mime


# ─────────────────────────── 历史档案 ───────────────────────────

UNEXPORTED_TTL = 7 * 86400   # 未存 HTML 的工作记录保留 7 天后自动清理（档案只认 HTML）


def _purge_record(rid):
    """物理删除一条记录：工作目录 + 发布视频 + HTML 存档"""
    d = _rec_dir(rid)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    for p in (os.path.join(NGINX_VIDEO_DIR, rid + '.mp4'),
              os.path.join(EXPORT_DIR, re.sub(r'[^\w.-]', '', rid) + '.html')):
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def history_list():
    """历史档案：只有点过「保存为 HTML」的记录才算档案（用户 2026-09-24 规则）；
    未存档的工作记录不显示，且超过 TTL 顺手物理清理"""
    with _index_lock:
        items = _load_index()
        now = int(time.time())
        keep, stale = [], []
        for it in items:
            rec = _load_record(it.get('id'))
            if (rec and not rec.get('exported')
                    and now - int(rec.get('created_at') or 0) > UNEXPORTED_TTL):
                stale.append(it.get('id'))
            else:
                keep.append(it)
        for rid in stale:
            _purge_record(rid)
        if stale:
            _save_index(keep)
        out = []
        for it in keep:
            rec = _load_record(it['id'])
            if not rec or not rec.get('exported'):
                continue    # 未保存为 HTML → 不形成档案
            an = rec.get('animation') or {}
            it['anim_status'] = an.get('status', 'none')
            it['has_video'] = an.get('status') == 'done' and bool(an.get('video_url'))
            with _task_lock:
                t = _tasks.get(it['id'])
            if t and t.get('status'):
                it['anim_status'] = t['status']
                it['has_video'] = bool(t.get('video_url')) or it['has_video']
            it['exported'] = True
            it['export_url'] = rec.get('export_url')
            out.append(it)
    return {'ok': True, 'items': out}


def history_get(rid):
    rec = _load_record(rid)
    if not rec:
        return {'ok': False, 'error': '记录不存在'}
    return {'ok': True, 'record': _public_record(rec)}


def history_delete(rid):
    with _index_lock:
        items = [i for i in _load_index() if i.get('id') != rid]
        _save_index(items)
    _purge_record(rid)
    return {'ok': True}


# ─────────────────────────── 动画脚本 ───────────────────────────

ANIM_SYS = '你是科普视频分镜师。把题目讲解改写成解说动画分镜。只输出严格 JSON，不要代码围栏。'

ANIM_USER_TMPL = '''根据下面的题目与解答，产出解说动画分镜 JSON：
{{
 "scenes": [
   {{"heading": "场景大标题（≤12字）",
     "narration": "该场景解说词（一句话说清，30~70字，语气像老师口头讲解）",
     "visual": {{"kind": "title|problem|steps|svg|answer",
                 "lines": ["画面上显示的要点行（每行≤24字，1~5行）"],
                 "svg_index": 0}}}}
 ]
}}
硬性要求：
1. 场景 4~8 个，第一个必须是 kind=title（lines 给副标题一句话），最后必须是 kind=answer；
2. 有解题步骤时用 kind=steps 把关键步骤拆成 1~2 个场景，lines 是提炼后的要点（不是原文照搬）；
3. 解答中如有 SVG 图（共 {nsvg} 张），安排 kind=svg 场景引用对应 svg_index（从 0 起）；没有图就不要用 svg；
4. problem 场景的 lines 把题干压缩成 ≤4 行要点；
5. 所有画面文字禁止 LaTeX，数学式改写为 Unicode 纯文本（x²、√2、π、½、a/b、△ABC、∠A=90°）；
6. narration 覆盖讲解逻辑：为什么这么做 → 怎么做 → 得到什么。

【题目】{problem}
【答案】{answer}
【思路】{summary}
【步骤】
{steps_text}
【SVG图数量】{nsvg}'''


def _build_anim_script(rec):
    sol = rec.get('solution') or {}
    steps_text = '\n'.join('%d. %s：%s' % (i + 1, st['heading'], st['detail'][:300])
                           for i, st in enumerate(sol.get('steps') or []))
    prompt = ANIM_USER_TMPL.format(
        problem=rec.get('problem', '')[:1200],
        answer=sol.get('answer', '')[:200],
        summary=sol.get('summary', '')[:300],
        steps_text=steps_text[:3000],
        nsvg=len(sol.get('svgs') or []),
    )
    raw = _call_llm(ANIM_SYS, prompt, temperature=0.5, max_tokens=6000)
    obj = _extract_json(raw)
    scenes = obj.get('scenes') or []
    clean = []
    nsvg = len(sol.get('svgs') or [])
    for sc in scenes[:8]:
        vis = sc.get('visual') or {}
        kind = vis.get('kind') if vis.get('kind') in ('title', 'problem', 'steps', 'svg', 'answer') else 'steps'
        if kind == 'svg' and (not isinstance(vis.get('svg_index'), int) or vis['svg_index'] >= nsvg):
            kind = 'steps'
        lines = [str(x)[:60] for x in (vis.get('lines') or [])][:5]
        narration = str(sc.get('narration') or '')[:160]
        heading = str(sc.get('heading') or '')[:20]
        if not narration and not lines:
            continue
        clean.append({'heading': heading, 'narration': narration,
                      'visual': {'kind': kind, 'lines': lines,
                                 'svg_index': vis.get('svg_index') if kind == 'svg' else 0}})
    if len(clean) < 2:
        raise ValueError('分镜过少，LLM 输出不可用')
    return clean


# ─────────────────────────── HyperFrames 合成 ───────────────────────────

_CSS = '''
@font-face { font-family: "Noto Sans CJK SC"; src: local("Noto Sans CJK SC"); }
* { margin:0; padding:0; box-sizing:border-box; }
html,body { width:1920px; height:1080px; overflow:hidden; background:#0b1020; }
#root { width:100%; height:100%; position:relative; overflow:hidden;
  font-family:"Noto Sans CJK SC",Inter,system-ui,sans-serif; color:#f2f5fa; }
.bgdeco { position:absolute; inset:0; background:
  radial-gradient(1200px 600px at 15% -10%, rgba(56,116,255,.22), transparent 60%),
  radial-gradient(1000px 500px at 110% 110%, rgba(255,120,60,.14), transparent 60%); }
.clip { position:absolute; inset:0; }
.sc-inner { position:absolute; left:120px; right:120px; top:110px; bottom:220px;
  display:flex; flex-direction:column; }
.sc-head { font-size:64px; font-weight:700; letter-spacing:.01em; color:#ffd166; margin-bottom:44px; }
.sc-head .tag { display:inline-block; font-size:30px; color:#9db4d8; font-weight:400; margin-left:22px; }
.sc-body { flex:1; display:flex; flex-direction:column; justify-content:center; gap:30px; }
.ln { font-size:44px; line-height:1.5; color:#e8eefb; padding-left:36px; position:relative; }
.ln::before { content:""; position:absolute; left:0; top:.62em; width:14px; height:14px;
  border-radius:4px; background:#4f8ef7; }
.big { font-size:60px; font-weight:700; color:#7dffb0; text-align:center; }
.hero { font-size:96px; font-weight:800; text-align:center; }
.sub { font-size:44px; color:#a8b8d8; text-align:center; margin-top:34px; }
.svgbox { display:flex; justify-content:center; align-items:center; flex:1; min-height:0; }
.svgbox svg { max-width:1300px; max-height:640px; background:#ffffff; border-radius:20px;
  padding:28px; box-shadow:0 18px 60px rgba(0,0,0,.45); }
.capbar { position:absolute; left:0; right:0; bottom:0; height:170px;
  background:linear-gradient(transparent, rgba(4,8,18,.92) 38%); display:flex;
  align-items:flex-end; justify-content:center; padding-bottom:44px; }
.cap-inner { max-width:1560px; font-size:40px; line-height:1.45; color:#ffffff;
  text-align:center; text-shadow:0 2px 10px rgba(0,0,0,.8); }
.prog { position:absolute; top:44px; right:64px; font-size:28px; color:#6f83a8; }
'''


def _scene_body_html(i, sc, svgs):
    vis = sc.get('visual') or {}
    kind = vis.get('kind', 'steps')
    lines = vis.get('lines') or []
    if kind == 'title':
        h = '<div class="sc-body" id="sc%d-b" style="align-items:center;">' % i
        h += '<div class="hero ln2">%s</div>' % esc_html(lines[0] if lines else '解题动画')
        if len(lines) > 1:
            h += '<div class="sub ln2">%s</div>' % esc_html(lines[1])
        return h + '</div>'
    if kind == 'answer':
        h = '<div class="sc-body" id="sc%d-b" style="align-items:center;">' % i
        h += '<div class="big ln2">%s</div>' % esc_html(lines[0] if lines else '')
        for ln in lines[1:]:
            h += '<div class="sub ln2">%s</div>' % esc_html(ln)
        return h + '</div>'
    if kind == 'svg' and svgs:
        idx = min(int(vis.get('svg_index') or 0), len(svgs) - 1)
        return '<div class="svgbox" id="sc%d-b">%s</div>' % (i, svgs[idx]['code'])
    h = '<div class="sc-body" id="sc%d-b">' % i
    for ln in lines:
        h += '<div class="ln">%s</div>' % esc_html(ln)
    return h + '</div>'


def build_composition(rec, scenes, vo=None):
    """生成 HyperFrames standalone index.html，返回 (html, total_duration)
    vo: 配音列表（与 scenes 等长，元素为 None 或 {'file','dur'}），None=无配音"""
    svgs = (rec.get('solution') or {}).get('svgs') or []
    # 每场景时长：有配音时以实际语音时长为准（+0.6s 尾距，下限 4.5s）；
    # 无配音时按解说词字数估算（≈4.2 字/秒）4~12s
    durs = []
    for i, sc in enumerate(scenes):
        v = vo[i] if vo and i < len(vo) else None
        if v:
            durs.append(round(max(4.5, min(30.0, v['dur'] + 0.6)), 1))
        else:
            n = len(sc.get('narration') or '')
            durs.append(round(min(12.0, max(4.5, n / 4.2 + 1.2)), 1))
    total = round(sum(durs) + 0.5, 1)

    clips, tl_lines = [], []
    t = 0.0
    for i, (sc, d) in enumerate(zip(scenes, durs)):
        head = esc_html(sc.get('heading') or '')
        kind = (sc.get('visual') or {}).get('kind')
        tag = {'title': '', 'problem': '题目', 'steps': '讲解', 'svg': '图示', 'answer': '答案'}.get(kind, '')
        taghtml = '<span class="tag">%s</span>' % tag if tag else ''
        clips.append(
            '<section class="clip" id="sc%d" data-start="%.2f" data-duration="%.2f" data-track-index="0">'
            '<div class="bgdeco"></div>'
            '<div class="sc-inner"><h2 class="sc-head" id="sc%d-h">%s%s</h2>%s</div>'
            '<div class="prog" id="sc%d-p">%d / %d</div>'
            '</section>' % (i, t, d, i, head, taghtml, _scene_body_html(i, sc, svgs), i, i + 1, len(scenes)))
        if sc.get('narration'):
            clips.append(
                '<section class="clip" id="cap%d" data-start="%.2f" data-duration="%.2f" data-track-index="1">'
                '<div class="capbar"><div class="cap-inner" id="cap%d-t">%s</div></div>'
                '</section>' % (i, t, d, i, esc_html(sc['narration'])))
            tl_lines.append("tl.fromTo('#cap%d-t',{opacity:0,y:14},{opacity:1,y:0,duration:.45,ease:'power2.out'},%.2f);" % (i, t + 0.25))
        v = vo[i] if vo and i < len(vo) else None
        if v:
            clips.append(
                '<audio id="vo%d" src="%s" data-start="%.2f" data-duration="%.2f" '
                'data-track-index="10" data-volume="1"></audio>'
                % (i, v['file'], t + 0.15, min(v['dur'] + 0.15, d)))
        tl_lines.append("tl.fromTo('#sc%d-h',{opacity:0,y:26},{opacity:1,y:0,duration:.55,ease:'power3.out'},%.2f);" % (i, t + 0.1))
        tl_lines.append("tl.fromTo('#sc%d .ln, #sc%d .ln2',{opacity:0,y:20},{opacity:1,y:0,duration:.5,stagger:.28,ease:'power2.out'},%.2f);" % (i, i, t + 0.35))
        tl_lines.append("tl.fromTo('#sc%d-p',{opacity:0},{opacity:1,duration:.4},%.2f);" % (i, t + 0.2))
        t = round(t + d, 2)

    html = '''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=1920, height=1080" />
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>%s</style>
</head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-width="1920" data-height="1080" data-duration="%.1f">
%s
</div>
<script>
const tl = gsap.timeline({ paused: true });
%s
window.__timelines["main"] = tl;
tl.seek(0);
</script>
</body>
</html>''' % (_CSS, total, '\n'.join(clips), '\n'.join(tl_lines))
    return html, total


_PKG = '''{
  "name": "%s",
  "private": true,
  "type": "module",
  "scripts": {
    "check": "npx --yes hyperframes@%s check",
    "render": "npx --yes hyperframes@%s render"
  }
}'''

_HFJSON = '''{
  "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
  "media": { "autoProxy": true }
}'''


# ─────────────────────────── 渲染任务 ───────────────────────────

def _set_task(rid, **kw):
    with _task_lock:
        t = _tasks.setdefault(rid, {})
        t.update(kw)
    rec = _load_record(rid)
    if rec:
        rec.setdefault('animation', {}).update({k: v for k, v in kw.items() if k != 'progress'})
        rec['animation']['progress'] = kw.get('progress', rec.get('animation', {}).get('progress'))
        _save_record(rec)


def animate(rid, voice=None):
    rec = _load_record(rid)
    if not rec:
        return {'ok': False, 'error': '记录不存在'}
    with _task_lock:
        t = _tasks.get(rid) or {}
        if t.get('status') in ('building', 'rendering', 'voicing'):
            return {'ok': False, 'error': '该题动画正在生成中'}
    voice = voice if isinstance(voice, dict) and voice.get('kind') in ('ali', 'heygen') else None
    th = threading.Thread(target=_animate_worker, args=(rid, voice), daemon=True)
    th.start()
    return {'ok': True, 'status': 'voicing' if voice else 'building'}


def _animate_worker(rid, voice=None):
    proj = os.path.join(_rec_dir(rid), 'project')
    try:
        rec = _load_record(rid)
        _set_task(rid, status='building', error=None, progress=0, started_at=time.time())
        scenes = _build_anim_script(rec)
        rec = _load_record(rid)
        rec['animation']['script'] = scenes
        rec['animation']['voice'] = voice
        _save_record(rec)

        if os.path.isdir(proj):
            shutil.rmtree(proj, ignore_errors=True)
        os.makedirs(proj, exist_ok=True)

        vo = None
        if voice:
            _set_task(rid, status='voicing', progress=1)
            vo = synthesize_voiceover(proj, scenes, voice)

        html, total = build_composition(rec, scenes, vo)
        with open(os.path.join(proj, 'index.html'), 'w', encoding='utf-8') as f:
            f.write(html)
        with open(os.path.join(proj, 'package.json'), 'w', encoding='utf-8') as f:
            f.write(_PKG % (rid, HF_VERSION, HF_VERSION))
        with open(os.path.join(proj, 'hyperframes.json'), 'w', encoding='utf-8') as f:
            f.write(_HFJSON)

        _set_task(rid, status='rendering', progress=2)
        env = dict(os.environ)
        env['PATH'] = os.path.dirname(NPX) + ':/usr/local/bin:/usr/bin:/bin'
        env['HOME'] = os.path.expanduser('~')
        cmd = [NPX, '--yes', 'hyperframes@' + HF_VERSION, 'render',
               '--quality', 'looks', '--output', 'out.mp4']
        proc = subprocess.Popen(cmd, cwd=proj, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors='replace')
        last_pct = 2
        for line in proc.stdout:
            m = re.search(r'(\d{1,3})%', line)
            if m:
                pct = min(97, max(last_pct, int(m.group(1))))
                if pct - last_pct >= 3:
                    last_pct = pct
                    with _task_lock:
                        _tasks.setdefault(rid, {})['progress'] = pct
        rc = proc.wait(timeout=RENDER_TIMEOUT)
        out_mp4 = os.path.join(proj, 'out.mp4')
        if rc != 0 or not os.path.exists(out_mp4) or os.path.getsize(out_mp4) < 1024:
            raise RuntimeError('hyperframes render 失败 (rc=%s)' % rc)

        keep = os.path.join(_rec_dir(rid), 'animation.mp4')
        shutil.copyfile(out_mp4, keep)
        pub = os.path.join(NGINX_VIDEO_DIR, rid + '.mp4')
        shutil.copyfile(out_mp4, pub)
        url = '/math-videos/%s.mp4' % rid
        _set_task(rid, status='done', progress=100, video_url=url, duration=total)
        with _index_lock:
            items = _load_index()
            for it in items:
                if it.get('id') == rid:
                    it['has_video'] = True
            _save_index(items)
    except Exception as e:
        try:
            _set_task(rid, status='failed', error=str(e)[:500])
        except Exception:
            pass


def anim_status(rid):
    rec = _load_record(rid)
    if not rec:
        return {'ok': False, 'error': '记录不存在'}
    an = rec.get('animation') or {}
    with _task_lock:
        t = dict(_tasks.get(rid) or {})
    status = t.get('status') or an.get('status') or 'none'
    # 服务重启后卡在 rendering/building/voicing 的判为中断
    if status in ('rendering', 'building', 'voicing') and not t:
        status = 'failed'
        an_err = '服务重启导致任务中断，请重新生成'
    else:
        an_err = t.get('error') or an.get('error')
    # 已运行时长（秒）：供前端刷新后接续计时
    started = t.get('started_at') or an.get('started_at')
    elapsed = None
    if started and status in ('rendering', 'building', 'voicing'):
        elapsed = max(0, int(time.time() - float(started)))
    return {'ok': True, 'status': status,
            'progress': t.get('progress', an.get('progress', 0)),
            'elapsed': elapsed,
            'video_url': an.get('video_url') if status == 'done' else None,
            'error': an_err if status == 'failed' else None}


# ─────────────────────────── 对话纠偏 ───────────────────────────

CHAT_SYS = ('你是解出这道题的老师，学生正在就解答提问或指出错误（题目识别错、题意理解错、计算错等）。'
            '认真复核；学生说得对就承认错误并给出完整修正后的解答。只输出严格 JSON，不要代码围栏。')

CHAT_USER_TMPL = '''【题目】
{problem}

【当前解答】
答案：{answer}
思路：{summary}
步骤：
{steps_text}

【之前的对话】
{history}

【学生最新发言】
{message}

输出 JSON：
{{"reply": "对学生的回应，LaTeX 行内用 \\(...\\)，友好清楚，不超过300字；若认错先说清哪里错了、正确的是什么", "correction": null}}
correction 规则：学生意见成立、答案/步骤确需修改时，给出完整修正解答 {{"answer":"","summary":"","knowledge":[],"steps":[{{"heading":"","detail":""}}],"svgs":[{{"caption":"","code":""}}]}}（与原解答同格式，SVG 规范同前：viewBox≤800宽、中文标注、禁 script；无需图给空数组）；原解答无需修改则为 null。'''


def _image_data_url(rec):
    """从档案目录读回题目图片的 data URL（无则 None）"""
    if not (rec.get('has_image') and rec.get('image_file')):
        return None
    p = os.path.join(_rec_dir(rec['id']), rec['image_file'])
    if not os.path.exists(p):
        return None
    ext = rec['image_file'].rsplit('.', 1)[-1].lower()
    mime = {'png': 'image/png', 'webp': 'image/webp', 'gif': 'image/gif'}.get(ext, 'image/jpeg')
    with open(p, 'rb') as f:
        return 'data:%s;base64,%s' % (mime, base64.b64encode(f.read()).decode())


def chat(rid, message):
    """就某题与 AI 对话；若 AI 接受纠正则更新存档解答"""
    rec = _load_record(rid)
    if not rec:
        return {'ok': False, 'error': '记录不存在'}
    message = (message or '').strip()
    if not message:
        return {'ok': False, 'error': '消息为空'}
    message = message[:2000]
    sol = rec.get('solution') or {}
    steps_text = '\n'.join('%d. %s：%s' % (i + 1, st['heading'], st['detail'][:400])
                           for i, st in enumerate(sol.get('steps') or []))
    history = '\n'.join('%s：%s' % ('学生' if c.get('role') == 'user' else '老师',
                                   str(c.get('text', ''))[:300])
                        for c in (rec.get('chats') or [])[-12:]) or '（无）'
    prompt = CHAT_USER_TMPL.format(problem=rec.get('problem', '')[:2500],
                                   answer=sol.get('answer', '')[:300],
                                   summary=sol.get('summary', '')[:400],
                                   steps_text=steps_text[:4000],
                                   history=history[:3000],
                                   message=message)
    img = _image_data_url(rec)
    if img:
        content = [{'type': 'image_url', 'image_url': {'url': img}},
                   {'type': 'text', 'text': prompt + '\n\n（附题目原图，若学生质疑识别错误，请仔细重新读图核对。）'}]
        raw = _call_llm(CHAT_SYS, content, model=VL_MODEL, temperature=0.3, max_tokens=12000)
    else:
        raw = _call_llm(CHAT_SYS, prompt, temperature=0.3, max_tokens=12000)
    try:
        obj = _extract_json(raw)
    except Exception:
        obj = {'reply': (raw or '')[:1500], 'correction': None}  # 容错：非 JSON 当纯回复
    reply = str(obj.get('reply') or '').strip() or '（无内容）'
    correction = obj.get('correction')
    updated = False
    if isinstance(correction, dict):
        cleaned = _clean_solution(correction)
        if cleaned:
            steps2, svgs2 = cleaned
            sol['answer'] = str(correction.get('answer') or sol.get('answer', ''))
            sol['summary'] = str(correction.get('summary') or sol.get('summary', ''))
            kn = correction.get('knowledge')
            if isinstance(kn, list) and kn:
                sol['knowledge'] = [str(k)[:30] for k in kn][:8]
            sol['steps'] = steps2
            # 纠正未提供新图时保留原有 SVG，避免改文字步骤把图丢了
            if svgs2:
                sol['svgs'] = svgs2
            rec['solution'] = sol
            updated = True
    chats = rec.setdefault('chats', [])
    chats.append({'role': 'user', 'text': message, 'ts': int(time.time())})
    chats.append({'role': 'ai', 'text': reply[:3000], 'ts': int(time.time()), 'corrected': updated})
    rec['chats'] = chats[-40:]
    rec['updated_at'] = int(time.time())
    _save_record(rec)
    return {'ok': True, 'reply': reply, 'updated': updated,
            'solution': rec['solution'] if updated else None}


# ─────────────────────────── 导出 HTML 存档 ───────────────────────────

EXPORT_DIR = '/usr/share/nginx/html/math-exports'
MAX_EMBED_VIDEO = 40 * 1024 * 1024   # 超过则不内嵌

_EXPORT_PAGE = '''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · 解题档案</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
<style>
body{font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;background:#f4f6fb;color:#1f2937;margin:0;padding:32px 16px}
.page{max-width:860px;margin:0 auto;background:#fff;border-radius:16px;box-shadow:0 8px 30px rgba(15,23,42,.08);padding:40px 44px}
h1{font-size:26px;margin:0 0 6px}
.meta{color:#6b7280;font-size:13px;margin-bottom:16px}
.chip{display:inline-block;background:#eef2ff;color:#4338ca;border-radius:999px;padding:3px 12px;font-size:12px;margin:0 6px 6px 0}
h2{font-size:17px;color:#b45309;border-bottom:2px solid #fde68a;padding-bottom:6px;margin:30px 0 14px}
.problem{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:16px 18px;font-size:14px;line-height:1.9;white-space:pre-wrap}
.problem img{max-width:100%;border-radius:8px;margin-top:10px;display:block}
.answer{background:#f0fdf4;border-left:4px solid #22c55e;border-radius:8px;padding:14px 18px;font-size:16px;line-height:1.8}
.summary{background:#eff6ff;border-left:4px solid #3b82f6;border-radius:8px;padding:12px 18px;font-size:14px;line-height:1.8;color:#1e40af}
.step{border:1px solid #e5e7eb;border-radius:10px;margin:10px 0;overflow:hidden}
.step-h{background:#f9fafb;padding:10px 16px;font-weight:700;font-size:14px;color:#92400e}
.step-d{padding:12px 16px;font-size:14px;line-height:1.9;color:#374151}
.svgfig{text-align:center;margin:16px 0}
.svgfig svg{max-width:100%;height:auto;background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:8px}
.cap{font-size:12px;color:#6b7280;margin-top:6px}
video{width:100%;border-radius:12px;background:#000}
.chat{border-radius:10px;padding:10px 14px;margin:8px 0;font-size:14px;line-height:1.7}
.chat.u{background:#eef2ff;margin-left:12%}
.chat.a{background:#f8fafc;border:1px solid #e5e7eb;margin-right:12%}
.chat .fix{color:#16a34a;font-weight:700;font-size:12px}
.foot{text-align:center;color:#9ca3af;font-size:12px;margin-top:36px}
@media print{body{background:#fff;padding:0}.page{box-shadow:none;padding:10px}}
</style>
</head>
<body>
<div class="page">
<h1>__TITLE__</h1>
<div class="meta">学科：__SUBJECT__ · 解题时间：__CREATED__ · 导出时间：__EXPORTED__</div>
<div>__CHIPS__</div>
<h2>📋 题目</h2>
<div class="problem">__PROBLEM__</div>
<h2>✅ 最终答案</h2>
<div class="answer tex">__ANSWER__</div>
__SUMMARY__
<h2>📖 详细解答</h2>
__STEPS__
__SVGS__
__CHAT__
__VIDEO__
<div class="foot">本页面由 VIBE CODE「动画化解题」模块导出 · 自包含存档（视频/图片已内嵌）</div>
</div>
<script>
document.addEventListener("DOMContentLoaded",function(){
  if(window.renderMathInElement) renderMathInElement(document.body,{delimiters:[
    {left:"$$",right:"$$",display:true},{left:"\\\\[",right:"\\\\]",display:true},
    {left:"\\\\(",right:"\\\\)",display:false},{left:"$",right:"$",display:false}],throwOnError:false});
});
</script>
</body>
</html>'''


def export_html(rid):
    """把最终解答+对话+动画导出为自包含 HTML，存到 nginx 静态目录"""
    rec = _load_record(rid)
    if not rec:
        return {'ok': False, 'error': '记录不存在'}
    sol = rec.get('solution') or {}
    title = esc_html(rec.get('title') or '解题档案')
    chips = ''.join('<span class="chip">📌 %s</span>' % esc_html(k)
                    for k in (sol.get('knowledge') or []))
    # 题目（含内嵌图片）
    p_html = esc_html(rec.get('problem') or '')
    img = _image_data_url(rec)
    if img:
        p_html += '<img src="%s" alt="题目图片">' % img
    summary_html = ''
    if sol.get('summary'):
        summary_html = '<h2>💡 思路</h2><div class="summary tex">%s</div>' % esc_html(sol['summary'])
    steps_html = ''.join(
        '<div class="step"><div class="step-h">%d. <span class="tex">%s</span></div>'
        '<div class="step-d tex">%s</div></div>' % (i + 1, esc_html(st['heading']),
                                                    esc_html(st['detail']).replace('\n', '<br>'))
        for i, st in enumerate(sol.get('steps') or []))
    svgs_html = ''.join('<div class="svgfig">%s<div class="cap">%s</div></div>'
                        % (s['code'], esc_html(s.get('caption') or ''))
                        for s in (sol.get('svgs') or []))
    chats = rec.get('chats') or []
    chat_html = ''
    if chats:
        rows = []
        for c in chats:
            who = '🙋 学生' if c.get('role') == 'user' else '🤖 老师'
            fix = ' <span class="fix">✔ 已据此修正解答</span>' if c.get('corrected') else ''
            rows.append('<div class="chat %s"><b>%s%s</b><br>%s</div>' % (
                'u' if c.get('role') == 'user' else 'a', who, fix,
                esc_html(str(c.get('text', ''))).replace('\n', '<br>')))
        chat_html = '<h2>💬 对话纠偏记录</h2>' + ''.join(rows)
    an = rec.get('animation') or {}
    video_html = ''
    mp4 = os.path.join(_rec_dir(rid), 'animation.mp4')
    if an.get('status') == 'done' and os.path.exists(mp4):
        size = os.path.getsize(mp4)
        if size <= MAX_EMBED_VIDEO:
            with open(mp4, 'rb') as f:
                b64v = base64.b64encode(f.read()).decode()
            video_html = '<h2>🎬 解说动画</h2><video controls src="data:video/mp4;base64,%s"></video>' % b64v
        else:
            video_html = ('<h2>🎬 解说动画</h2><p style="font-size:13px;color:#6b7280">'
                          '视频 %.1fMB 过大未内嵌，请在服务器在线查看：%s</p>'
                          % (size / 1048576.0, esc_html(an.get('video_url') or '')))
    html = _EXPORT_PAGE
    for k, v in {
        '__TITLE__': title,
        '__SUBJECT__': esc_html(rec.get('subject') or '其他'),
        '__CREATED__': time.strftime('%Y-%m-%d %H:%M', time.localtime(rec.get('created_at', 0))),
        '__EXPORTED__': time.strftime('%Y-%m-%d %H:%M'),
        '__CHIPS__': chips,
        '__PROBLEM__': p_html,
        '__ANSWER__': esc_html(sol.get('answer') or '—'),
        '__SUMMARY__': summary_html,
        '__STEPS__': steps_html,
        '__SVGS__': svgs_html,
        '__CHAT__': chat_html,
        '__VIDEO__': video_html,
    }.items():
        html = html.replace(k, v)
    try:
        os.makedirs(EXPORT_DIR, exist_ok=True)
    except OSError as e:
        return {'ok': False, 'error': '导出目录不可写: %s' % e}
    fname = '%s.html' % re.sub(r'[^\w.-]', '', rid)
    outp = os.path.join(EXPORT_DIR, fname)
    with open(outp, 'w', encoding='utf-8') as f:
        f.write(html)
    try:  # 档案目录留一份副本
        with open(os.path.join(_rec_dir(rid), 'export.html'), 'w', encoding='utf-8') as f:
            f.write(html)
    except OSError:
        pass
    rec['exported'] = True
    rec['export_url'] = '/math-exports/' + fname
    rec['exported_at'] = int(time.time())
    _save_record(rec)
    return {'ok': True, 'export_url': rec['export_url'], 'size': os.path.getsize(outp),
            'has_video': bool(video_html and 'base64' in video_html)}
