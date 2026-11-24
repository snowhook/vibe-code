#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""八字 AI 大师详批引擎 — 混合模式：
规则引擎（bazi_service/bazi_deep）产出结构化命理数据 →
喂给大模型写深度评述（总评/重点深化/心法/校准）。
LLM 配置读自 ~/.openclaw/openclaw.json 的 models.providers。
"""
import os
import json
import urllib.request
import urllib.error
import threading

CONFIG_PATH = os.path.expanduser('~/.openclaw/openclaw.json')
PROVIDER = 'qwenTokenPersonalProvider'
MODEL = 'qwen3.7-plus'          # 详批模型（可改 qwen3.8-max-preview 提质）
TIMEOUT = 240

_cache_lock = threading.Lock()

def _get_api_key():
    with open(CONFIG_PATH, encoding='utf-8') as f:
        cfg = json.load(f)
    prov = cfg.get('models', {}).get('providers', {}).get(PROVIDER, {})
    key = prov.get('apiKey', '')
    base = prov.get('baseUrl', '')
    if not key or key.startswith('{') or not base:
        raise RuntimeError(f'LLM provider 未配置或 apiKey 为 SecretRef（{PROVIDER}）')
    return base.rstrip('/') + '/chat/completions', key

def _call_llm(system_prompt, user_prompt, temperature=0.8, max_tokens=4000):
    url, key = _get_api_key()
    body = json.dumps({
        'model': MODEL,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        'temperature': temperature,
        'max_tokens': max_tokens,
    }, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST', headers={
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {key}',
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    return data['choices'][0]['message']['content']

def _extract_json(text):
    """从 LLM 回复中稳健提取 JSON"""
    t = text.strip()
    # 去掉 markdown 代码围栏
    if t.startswith('```'):
        t = t.split('```', 2)
        for part in t:
            part = part.strip()
            if part.startswith('json'):
                part = part[4:].strip()
            if part.startswith('{'):
                t = part
                break
    # 找最外层大括号
    start = t.find('{')
    if start < 0:
        raise ValueError('回复中未找到JSON')
    depth = 0
    for i, ch in enumerate(t[start:], start):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i + 1])
    raise ValueError('JSON不完整')

# ── Prompt 构建 ──────────────────────────────────────────
SYSTEM_PROMPT = '''你是一位执业三十年、学养深厚的命理师，精通子平八字，风格接近《滴天髓》今注与盲派实务的融合：断语直接、意象具体、不说车轱辘话、不恐吓也不谄媚。

你将收到一份由规则引擎排好的命盘结构化数据（四柱、旺衰评分、喜用忌神、十神分布、大运、流年吉凶评级表、格局）。规则引擎的判断是基础，但你是最终解读人：
1. 你的任务是把这些数据"翻译"成有洞察、有画面感、对命主真实有用的论述。
2. 敢于下判断。不要"可能也许大概"堆砌，给出倾向性结论，再给条件（何时应验、何种情况例外）。
3. 语言要有金句感——像"累不是命不好，是能力太强又舍不得用在'少'处"这样一句话点透的表达能力。
4. 尊重规则引擎的数据，但如果你发现旺衰或用神判断有明显可商榷处（如从格边界、调候与扶抑冲突），在校准字段中直言。
5. 全部用简体中文。不用 markdown 标记，纯文本段落。

输出必须是严格的 JSON（不要代码围栏），结构：
{
  "verdict": "总评，180-280字，开篇即点透这个盘的核心矛盾与一生主线",
  "deepen": [
    {"title": "重点项名称（与输入的重点分析项一致）", "text": "200-400字的深化评述：规则引擎说了现象，你要说透成因、应期、具体建议"}
  ],
  "heartfa": ["心法金句1（20-40字）", "金句2", "金句3"],
  "calibration": "对规则引擎判断的校准意见，50-150字；若完全认同则为空字符串"
}

deepen 数组只包含输入中给出的重点分析项，顺序一致，每项都要有实质内容。'''

def build_prompt(result):
    """把规则引擎结果整理成 user prompt"""
    p = result['pillars']
    ys = result['yongshen']
    st = result['strength']
    tg = result.get('ten_gods', {}).get('stats', {})
    dayun = result.get('dayun', {})

    lines = []
    lines.append(f'命主：{result.get("name","")}，{result.get("gender","")}，'
                 f'公历 {result.get("birth_solar","")}（{result.get("birth_lunar","")}），生于{result.get("city","")}。')
    lines.append(f'四柱：{p["year"]}年 {p["month"]}月 {p["day"]}日 {p["hour"]}时')
    lines.append(f'日主：{result.get("day_master","")}')
    lines.append(f'旺衰：{st["level_name"]}（评分{st["score"]}）｜' + '；'.join(f'{d["name"]}{d["score"]:+d}' for d in st['details']))
    lines.append(f'用神：{ys["yongshen"]}　喜神：{ys["xishen"]}　忌神：{"、".join(ys["jishen"])}'
                 + (f'　调候：{ys["tiaohou"]}' if ys.get('tiaohou') else '')
                 + (f'　格局倾向：{ys["cong"]}格' if ys.get('cong') else ''))
    lines.append('取用理由：' + '；'.join(ys.get('reasons', [])))
    wc = ys.get('wx_count', {})
    lines.append('五行力量：' + ' '.join(f'{k}{v}' for k, v in wc.items()))
    if tg:
        lines.append('十神分布：' + '、'.join(f'{k}×{v}' for k, v in tg.items()))

    dl = dayun.get('dayun_list', [])
    if dl:
        lines.append(f'起运：{dayun.get("start_age_description","")}（{dayun.get("direction","")}）')
        lines.append('大运：' + ' → '.join(f'{d["gan_zhi"]}({d.get("year_range","")})' for d in dl[:9]))

    # 重点分析项（规则引擎已生成的内容，让LLM在其上深化）
    focus = result.get('focus', [])
    if focus:
        lines.append('')
        lines.append('规则引擎已生成的重点分析（你要在其基础上深化，不要照抄）：')
        for f in focus:
            if f.get('title') == '总结与补运':
                continue
            lines.append(f'◆ {f["title"]}:')
            for s in f.get('sections', []):
                t = s.get('type')
                if t == 'text':
                    lines.append('  ' + s['content'])
                elif t == 'quote':
                    lines.append('  ' + s['content'])
                elif t == 'list':
                    for it in s.get('items', [])[:6]:
                        lines.append('  - ' + str(it))
                elif t == 'table':
                    hdr = '|'.join(str(h) for h in s.get('headers', []))
                    lines.append(f'  [表]{hdr}')
                    for r in s.get('rows', [])[:12]:
                        lines.append('  |' + '|'.join(str(c)[:40] for c in r))

    lines.append('')
    lines.append(f'请对以上命盘输出总评，并深化这些重点分析项：{"、".join(f["title"] for f in focus if f.get("title") != "总结与补运")}')
    return '\n'.join(lines)

# ── 主入口 ───────────────────────────────────────────────
def ai_review(result):
    """对一次 analyze 结果生成 AI 详批，返回结构化 dict"""
    user_prompt = build_prompt(result)
    raw = _call_llm(SYSTEM_PROMPT, user_prompt)
    try:
        parsed = _extract_json(raw)
    except Exception:
        # LLM 没按 JSON 输出时降级：全文当总评
        parsed = {'verdict': raw[:1200], 'deepen': [], 'heartfa': [], 'calibration': ''}
    out = {
        'ok': True,
        'model': MODEL,
        'verdict': str(parsed.get('verdict', '')).strip(),
        'deepen': [{'title': str(d.get('title', '')), 'text': str(d.get('text', ''))}
                   for d in parsed.get('deepen', []) if isinstance(d, dict) and d.get('text')],
        'heartfa': [str(h) for h in parsed.get('heartfa', []) if h],
        'calibration': str(parsed.get('calibration', '')).strip(),
    }
    return out
