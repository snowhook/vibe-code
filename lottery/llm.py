# -*- coding: utf-8 -*-
"""llm.py — 大模型投注建议（OpenAI 兼容接口，后台可配置）"""
import json
import time
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


def _migrate(saved):
    """把旧版「单份配置」兼容成「多家并存 + 一个 active」结构。

    旧版是扁平 dict（enabled/base_url/api_key/model/timeout/temperature）；
    新版是 {active, enabled, providers:{key:{...}}}。每次读取都先归一，
    这样老库（含当前线上那份）打开即自动迁移，无需手工跑迁移脚本。
    """
    if isinstance(saved, dict) and 'providers' in saved and isinstance(saved.get('providers'), dict):
        saved.setdefault('active', next(iter(saved['providers']), 'deepseek'))
        saved.setdefault('enabled', False)
        return saved
    if not isinstance(saved, dict):
        saved = {}
    # 按 base_url 反查属于哪家预设；查不到归 custom；为空默认 deepseek
    base = (saved.get('base_url') or '').rstrip('/')
    key = 'deepseek' if not base else 'custom'
    if base:
        for p in PRESETS:
            if p['base_url'].rstrip('/') == base:
                key = p['key']; break
    prov = {p['key']: dict(DEFAULT) for p in PRESETS}
    prov[key].update({kk: saved[kk] for kk in ('base_url', 'api_key', 'model', 'timeout', 'temperature')
                      if kk in saved})
    return {'active': key, 'enabled': bool(saved.get('enabled', False)), 'providers': prov}


def raw_config():
    """完整存储结构 {active, enabled, providers:{key:{...}}}（含旧版迁移）。"""
    return _migrate(store.get_setting('llm', {}))


def get_config():
    """返回「当前启用服务商」合并后的单份配置，供 chat() 使用。"""
    raw = raw_config()
    key = raw.get('active') or 'deepseek'
    p = (raw.get('providers') or {}).get(key) or dict(DEFAULT)
    cfg = dict(DEFAULT)
    cfg.update(p)
    cfg['enabled'] = raw.get('enabled', False)
    cfg['active'] = key
    return cfg


def save_config(incoming):
    """incoming: {active, enabled, config:{base_url,model,api_key?,timeout,temperature}}。

    **只更新被选中的那一家**，其它服务商配置原样保留 —— 这就是「切换只需开关、
    不用重填 KEY」的实现基础：每家的 base_url/model/key 在库里各自独立存着。
    """
    raw = raw_config()
    key = incoming.get('active') or raw.get('active') or 'deepseek'
    if key not in (raw.get('providers') or {}):
        raw.setdefault('providers', {})[key] = dict(DEFAULT)
    pc = raw['providers'][key]
    cfg = incoming.get('config') or {}
    for f in ('base_url', 'model', 'timeout', 'temperature'):
        if f in cfg and cfg[f] is not None:
            pc[f] = cfg[f]
    if cfg.get('api_key'):          # 留空 = 不修改，沿用已存 key
        pc['api_key'] = cfg['api_key']
    raw['active'] = key
    raw['enabled'] = bool(incoming.get('enabled'))
    store.set_setting('llm', raw)
    return get_config()


def mask(raw):
    """给设置页用的脱敏结构：每家服务商各自回显 base_url/model/has_key/掩码。"""
    out = {'active': raw.get('active'), 'enabled': raw.get('enabled', False), 'providers': {}}
    for k, p in (raw.get('providers') or {}).items():
        key = p.get('api_key') or ''
        out['providers'][k] = {
            'base_url': p.get('base_url', ''),
            'model': p.get('model', ''),
            'timeout': p.get('timeout', 60),
            'temperature': p.get('temperature', 0.7),
            'has_key': bool(key),
            'api_key_masked': (key[:4] + '****' + key[-4:]) if len(key) > 12 else ('****' if key else ''),
        }
    return out


def _read_body(url, payload, api_key, total_timeout):
    """在「总时长」上限内读完响应体。

    三个坑叠在一起，才让「点评卡住」变成真·卡死：

    1) `urllib.request.urlopen(timeout=T)` 里的 T 是**每次 socket 操作**的超时，
       不是整个请求的总时长上限 —— 上游只要持续慢速吐字节，就可以无限拖下去。
       实测踩过：点评请求挂到 180 秒客户端都超时断开了，服务端这边还在读
       （日志里留下 BrokenPipeError，因为客户端早走了）。
    2) `r.read(N)` 会**阻塞直到读满 N 字节或流结束**。读块取小也没用 ——
       只要上游每次只发几个字节，一次 `read(4096)` 照样能拖穿总时长。
    3) 真正的解法是 `r.read1(N)`：它是 BufferedReader 的**单次系统调用**，
       有数据立刻返回、不会等满。所以循环里每次都能回到顶部核对 deadline。

    因此：优先用 read1（拿不到就退回 read），读块 4KB，socket timeout 也设成
    total_timeout（保证单次系统调用不会无限卡），三层一起兜住总时长。
    """
    req = urllib.request.Request(url, data=payload, headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + api_key,
    })
    with urllib.request.urlopen(req, timeout=total_timeout) as r:
        raw = getattr(r, 'raw', None)
        rd = getattr(raw, 'read1', None) or getattr(r, 'read1', None) or r.read
        chunks, total = [], 0
        deadline = time.time() + total_timeout
        while True:
            if time.time() > deadline:
                raise TimeoutError('响应超过 %d 秒仍未读完，已放弃' % total_timeout)
            chunk = rd(4096)        # read1：有数据就回，不等满
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    return b''.join(chunks)


def chat(messages, cfg=None, timeout=None):
    cfg = cfg or get_config()
    if not cfg.get('enabled'):
        return None, '大模型未启用'
    if not cfg.get('api_key'):
        return None, '未配置 API Key'
    total = int(timeout or cfg.get('timeout', 60))
    url = cfg['base_url'].rstrip('/') + '/chat/completions'
    payload = json.dumps({
        'model': cfg['model'],
        'messages': messages,
        'temperature': cfg.get('temperature', 0.7),
        'stream': False,
    }).encode('utf-8')
    try:
        raw = _read_body(url, payload, cfg['api_key'], total)
        data = json.loads(raw.decode('utf-8', 'replace'))
        txt = (data.get('choices') or [{}])[0].get('message', {}).get('content', '')
        if not txt:
            return None, '大模型返回了空内容'
        return txt, None
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode('utf-8', 'replace')[:300]
        except Exception:
            detail = ''
        msg = 'HTTP %s %s' % (e.code, detail)
        # 429/5xx 是**可重试**的临时故障（上游繁忙、限流），值得原地退避重试；
        # 4xx 里只有 429 重试，其余（401 Key 错、400 参数错）重试多少次都一样。
        if e.code == 429 or 500 <= e.code < 600:
            return None, msg + '（上游繁忙，可稍后重试）'
        return None, msg
    except TimeoutError as e:
        return None, '%s（已等待 %d 秒）' % (e, total)
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
    '「不同号码组合数」是去重后真正不同的组合个数。不要把它们混为一谈；\n'
    '4) **号码明细通常只给了样本组**（字段「自选池号码_每组一组」/「号码样本_最多12组」），'
    '组数多时明细已省略。请依据「各区号码统计」（每组号码个数、全池覆盖号码范围、'
    '全池落在低号区间的组数）和「完全重复的组数」来说明结构，'
    '**不要逐组点评、不要假装看过全部号码，也不要因为看不到明细就说无法分析**；\n'
    '5) 点评只覆盖三件事：① 这批号码的结构特征（是否分散/是否大量落在小号或生日区间、有没有跨注重号）；'
    '② 成本与期望回报（用给到的组数、注数、金额、回报率说话）；③ 风险提示（未中奖是常态、量入为出）；\n'
    '6) 若「完全重复的组数」大于 0，要指出重复的组不会提高中奖概率、只是把钱重复投入；\n'
    '7) 分点写，总长 250 字内，结论先行，用词克制、不煽情、不推销；\n'
    '8) 直接输出正文，不要使用 markdown 标题或代码块。'
)


def pool_review(context, cfg=None):
    """对「自选池」里的实际号码做点评。context 含彩种/玩法/自选池号码/组数/注数/概率等。

    timeout 取 min(配置值, 45)：点评只是 250 字的三点式说明，45 秒足够；
    上游繁忙时早失败早提示，比让用户对着「正在请求…」干等要好。
    前端另有90 秒的AbortController 兜底，这里是服务端这边的独立上限。
    """
    cfg = cfg or get_config()
    to = min(int(cfg.get('timeout', 60) or 60), 45)
    user = ('以下是用户自选池里准备投注的号码，请做客观点评：\n'
            + json.dumps(context, ensure_ascii=False, indent=2))
    return chat([{'role': 'system', 'content': SYS_REVIEW},
                 {'role': 'user', 'content': user}], cfg, timeout=to)


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


def ai_pick(prompt, spec, game_name, play_name, sets=5, cfg=None, avoid=None,
           extra=None, feedback=None):
    """让大模型按用户提示词出号。返回 (原文, 错误)。

    avoid:    已给出的号码列表，补号时传入，要求模型给全新的组合。
    extra:    从用户提示词解析出的「硬性约束」（区间 / 奇偶比等），转成
              明确的数字范围后追加进 prompt —— 大模型对"1区/2区"这类口语
              经常自己发挥，必须给死数字范围 + 显式禁止，它才守得住。
    feedback: 上一轮因不满足约束被拒的注的说明，追加进去让模型针对性修正。
    """
    cfg = cfg or get_config()
    user = ('彩种：%s\n玩法：%s\n\n号码格式要求：\n%s\n\n用户的要求：\n%s\n'
            % (game_name, play_name, spec, prompt))
    if extra:
        user += '\n\n以下是从你的要求里提取出的【硬性约束】，必须满足：\n' + extra + '\n'
    if avoid:
        user += ('\n下面这些号码已经用过了，请给出与它们**完全不同**的新组合：\n%s\n'
                 % json.dumps(avoid, ensure_ascii=False))
    if feedback:
        user += ('\n上一轮生成的号码不满足以下约束，请修正后重新给：\n'
                 + '\n'.join('- ' + f for f in feedback) + '\n')
    user += '\n请给出 %d 注，严格按上述 JSON 格式输出。' % sets
    return chat([{'role': 'system', 'content': SYS_PICK},
                 {'role': 'user', 'content': user}], cfg)
