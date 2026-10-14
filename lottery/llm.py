# -*- coding: utf-8 -*-
"""llm.py — 大模型投注建议（OpenAI 兼容接口，后台可配置）"""
import json
import urllib.error
import urllib.request

import store

DEFAULT = {
    'enabled': False,
    'base_url': 'https://api.deepseek.com/v1',
    'api_key': '',
    'model': 'deepseek-chat',
    'timeout': 60,
    'temperature': 0.7,
}

PRESETS = [
    {'key': 'deepseek', 'name': 'DeepSeek', 'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat'},
    {'key': 'openai', 'name': 'OpenAI', 'base_url': 'https://api.openai.com/v1', 'model': 'gpt-4o-mini'},
    {'key': 'dashscope', 'name': '阿里百炼', 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'model': 'qwen-plus'},
    {'key': 'moonshot', 'name': 'Kimi', 'base_url': 'https://api.moonshot.cn/v1', 'model': 'moonshot-v1-8k'},
    {'key': 'zhipu', 'name': '智谱GLM', 'base_url': 'https://open.bigmodel.cn/api/paas/v4', 'model': 'glm-4-flash'},
    {'key': 'custom', 'name': '自定义', 'base_url': '', 'model': ''},
]


def get_config():
    cfg = dict(DEFAULT)
    saved = store.get_setting('llm', {})
    if isinstance(saved, dict):
        cfg.update(saved)
    return cfg


def save_config(cfg):
    cur = get_config()
    for k in DEFAULT:
        if k in cfg and cfg[k] is not None:
            cur[k] = cfg[k]
    store.set_setting('llm', cur)
    return cur


def mask(cfg):
    out = dict(cfg)
    k = out.get('api_key') or ''
    out['api_key_masked'] = (k[:4] + '****' + k[-4:]) if len(k) > 12 else ('****' if k else '')
    out['has_key'] = bool(k)
    out.pop('api_key', None)
    return out


def chat(messages, cfg=None, timeout=None):
    cfg = cfg or get_config()
    if not cfg.get('enabled'):
        return None, '大模型未启用'
    if not cfg.get('api_key'):
        return None, '未配置 API Key'
    url = cfg['base_url'].rstrip('/') + '/chat/completions'
    payload = json.dumps({
        'model': cfg['model'],
        'messages': messages,
        'temperature': cfg.get('temperature', 0.7),
        'stream': False,
    }).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + cfg['api_key'],
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout or cfg.get('timeout', 60)) as r:
            data = json.loads(r.read().decode('utf-8', 'replace'))
        txt = (data.get('choices') or [{}])[0].get('message', {}).get('content', '')
        return txt, None
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode('utf-8', 'replace')[:300]
        except Exception:
            detail = ''
        return None, 'HTTP %s %s' % (e.code, detail)
    except Exception as e:
        return None, '%s: %s' % (type(e).__name__, e)


SYS_PROMPT = (
    '你是彩票数据分析助手。必须严格遵守以下事实：\n'
    '1) 彩票每期独立，历史数据无法预测未来，任何「遗漏/冷热/规律」都不改变中奖概率；\n'
    '2) 不要暗示某组号码「该出了」；\n'
    '3) 只做客观的概率、期望回报、成本与风险说明；\n'
    '4) 语气简洁、结论先行，不要给购彩的情绪鼓励。'
)


def lottery_advice(context, cfg=None):
    """context: dict，含彩种/玩法/注数/概率等"""
    cfg = cfg or get_config()
    user = ('请基于以下客观数据，给出简短（200 字内）的投注视角说明：\n'
            + json.dumps(context, ensure_ascii=False, indent=2))
    return chat([{'role': 'system', 'content': SYS_PROMPT},
                 {'role': 'user', 'content': user}], cfg)


SYS_REVIEW = (
    '你是彩票数据助手，负责对用户「已经选好的一批号码」（自选池）做客观点评。必须严格遵守以下事实：\n'
    '1) 彩票每期独立，历史数据无法预测未来，任何「遗漏/冷热/结构/规律」都不改变这一批号码的中奖概率；\n'
    '2) 不要暗示某组号码「该出了」或「更容易中」；不要夸赞号码选得好，也不要给购彩的情绪鼓励；\n'
    '3) 用词要区分「组」和「注」：数据里的「自选池组数」是用户选了几组号码，'
    '「自选池注数_展开后」是这些号码按复式展开后的总注数（一组复式会展开成很多注），'
    '「自选池号码_每组一组」是每组的号码明细。不要把它们混为一谈；\n'
    '4) 点评只覆盖三件事：① 这批号码的结构特征（是否分散/是否大量落在小号或生日区间、有没有跨注重号）；'
    '② 成本与期望回报（用给到的组数、注数、金额、回报率说话）；③ 风险提示（未中奖是常态、量入为出）；\n'
    '5) 分点写，总长 250 字内，结论先行，用词克制、不煽情、不推销；\n'
    '6) 直接输出正文，不要使用 markdown 标题或代码块。'
)


def pool_review(context, cfg=None):
    """对「自选池」里的实际号码做点评。context 含彩种/玩法/自选池号码/组数/注数/概率等"""
    cfg = cfg or get_config()
    user = ('以下是用户自选池里准备投注的号码，请做客观点评：\n'
            + json.dumps(context, ensure_ascii=False, indent=2))
    return chat([{'role': 'system', 'content': SYS_REVIEW},
                 {'role': 'user', 'content': user}], cfg)


SYS_PICK = (
    '你是彩票选号助手。用户会给出自己的选号偏好或要求，你据此给出号码组合。\n'
    '必须严格遵守：\n'
    '1) 彩票每期独立，任何选号方式都不改变中奖概率；不得暗示某个号码「该出了」或「更容易中」；\n'
    '2) 你给出的只是「按用户偏好生成的号码组合」，不是预测，也不能提高中奖率；\n'
    '3) 每注号码的个数必须与「号码格式要求」**完全一致**——不多给、不少给、区内不重复；'
    '格式要求写的是几个就给几个（数量大于标准单注即为复式，照写即可）；'
    '不要额外解释或自行改成标准单注；\n'
    '4) tickets 数组里必须正好有用户要求的注数，不多不少；\n'
    '5) 只输出一个 JSON 对象，不要使用 markdown 代码块标记，不要输出任何解释性文字、前后缀或注释。\n'
)


def ai_pick(prompt, spec, game_name, play_name, sets=5, cfg=None, avoid=None):
    """让大模型按用户提示词出号。返回 (原文, 错误)。

    avoid: 已给出的号码列表，补号时传入，要求模型给全新的组合。
    """
    cfg = cfg or get_config()
    user = ('彩种：%s\n玩法：%s\n\n号码格式要求：\n%s\n\n用户的要求：\n%s\n'
            % (game_name, play_name, spec, prompt))
    if avoid:
        user += ('\n下面这些号码已经用过了，请给出与它们**完全不同**的新组合：\n%s\n'
                 % json.dumps(avoid, ensure_ascii=False))
    user += '\n请给出 %d 注，严格按上述 JSON 格式输出。' % sets
    return chat([{'role': 'system', 'content': SYS_PICK},
                 {'role': 'user', 'content': user}], cfg)
