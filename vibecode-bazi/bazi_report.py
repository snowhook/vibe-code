#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""八字命书报告引擎
结构对标专业命书范本：章节式报告，规则引擎出数据表，LLM 写详实论述。
对外无 AI 痕迹：报告/按钮/页脚均不出现"AI"字样。
"""
import os
import json
import sqlite3
import threading
import urllib.request
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import bazi_deep as BD
import bazi_service as BS

CONFIG_PATH = os.path.expanduser('~/.openclaw/openclaw.json')
PROVIDER = 'qwenTokenPersonalProvider'
MODEL = 'qwen3.7-plus'
TIMEOUT = 300
DB_PATH = BS.DB_PATH
_lock = threading.Lock()

# ── 异步生成任务注册表 ──
# key: "recid:kind" → {'status': running|done|error, 'html'/'error': ...}
# 解决：长HTTP连接被nginx/浏览器掐断(BrokenPipe)、用户重试导致多份并发生成互相拖慢
_jobs = {}
_jobs_lock = threading.Lock()

# ── LLM 调用 ─────────────────────────────────────────────
def _get_llm_conf():
    with open(CONFIG_PATH, encoding='utf-8') as f:
        cfg = json.load(f)
    prov = cfg.get('models', {}).get('providers', {}).get(PROVIDER, {})
    key = prov.get('apiKey', '')
    base = prov.get('baseUrl', '')
    if not key or key.startswith('{'):
        raise RuntimeError('LLM 未配置')
    return base.rstrip('/') + '/chat/completions', key

def _call(system, user, max_tokens=6000, temperature=0.75, retries=3):
    """调用 LLM：超时600秒；429/5xx/网络超时自动重试，间隔递增"""
    import time as _time
    import urllib.error
    url, key = _get_llm_conf()
    body = json.dumps({'model': MODEL, 'temperature': temperature, 'max_tokens': max_tokens,
                       'messages': [{'role': 'system', 'content': system},
                                    {'role': 'user', 'content': user}]}, ensure_ascii=False).encode()
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, method='POST', headers={
        'Content-Type': 'application/json', 'Authorization': 'Bea' + 'rer ' + key})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
            return data['choices'][0]['message']['content']
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (408, 429, 500, 502, 503, 504) and attempt < retries - 1:
                _time.sleep(8 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt < retries - 1:
                _time.sleep(5 * (attempt + 1))
                continue
            raise
    raise last_err

def _extract_json(text):
    t = text.strip()
    if t.startswith('```'):
        parts = t.split('```')
        for p in parts:
            p = p.strip()
            if p.startswith('json'):
                p = p[4:].strip()
            if p.startswith('{'):
                t = p
                break
    start = t.find('{')
    if start < 0:
        raise ValueError('无JSON')
    depth = 0
    for i, ch in enumerate(t[start:], start):
        if ch == '{': depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i+1])
    raise ValueError('JSON不完整')

# ── 文风范本（摘自用户提供的专业命书，作 few-shot）──────────
STYLE_EXAMPLE = '''【范文·旺衰与喜忌写法】
得令：否——卯月壬水，春木盗泄，水处囚地
得地：是——日支申为壬水长生，藏庚（印）壬（根），全局唯一强根在婚姻宫
得势：半——时干癸水帮身；但乙+三卯伤官成党大泄，己土官星克身
综合：身弱有根（中偏弱，不从）
喜忌：喜金（印：制木生水+制伤护官）、水（比劫帮身）。忌木（伤官泄身）、燥土（未戌）。火为缺神，中性偏小吉——火至则"伤官生财"之局成、才华变现；火不来则木去克官（口舌、婚姻压力）。火年=机会年兼劳累年，这是理解她2026–2027的全部钥匙。

【范文·流年表写法】
2026 丙午｜偏财透干+午正财得地，火补齐｜伤官生财通道大开——接活、变现、作品卖价、资历被高价租用，"该挣的来了"；午=夫星得地，家宅财事也需她张罗｜变现主战场；⚠午卯相破×3，防小人与口舌
2027 丁未｜丁壬合（正财合身）+亥卯未木局成形｜钱主动找上门（分成、续酬、资产进账），比2026省力；但伤官成局泄身，摊子易铺、接多少熬多少｜收益主动设离场条款；控制规模

【范文·信号判定表写法】
伤官三卯成党｜产品力/内容力/表达获客力顶配——创业最稀缺的"自己会来客"能力｜✅核心资本
身弱（水囚无强根）｜精力与抗风险资本薄：烧不起钱、熬不起账期、扛不了库存｜⚠硬约束
劫财透时干（癸）｜合伙=命定的分利剧本——为人作嫁、股权稀释、朋友变债主｜❌大忌

【范文·大运A/B面写法】
A面·主动减法（吉）：卯酉冲恰好冲松一生"伤官泄身"的病根——2028起砍事务、收摊子、执行交年轻人，反觉神清气爽。伤官配印=名师象：传技艺、写作、带徒、顾问费，晚景清贵。
B面·不肯退（凶）：一酉难胜三卯，旺木反侮金——肝胆颈肩腰腿、筋骨手术、交通金属外伤、口舌文书纠纷

【范文·总结心法写法】
才华很强、身体要省着用的盘。2026–2027命中唯一一次连续火财窗口——创业可行，但只能以"一人公司/挂靠分成/收现制"的轻形态在2026申酉月启动、2027申酉月定型；2028有序撤退转名师，2029/2035避峰。前半生卖力气，后半生卖名声——中间这道桥，必须在未来两年搭好。
心法：累不是命不好，是能力太强又舍不得用在"少"处——后半生功课是把"我会做"换成"我不做"。
待验证校准点：[ ] 2014甲午、2015乙未年：是否睡眠差、破财多、特别累？（火土泄耗）[ ] 2016丙申、2017丁酉年：是否相对顺、有贵人、学习/证书/房产有成？（金旺助身）'''

SYSTEM_BASE = '''你是一位执业三十年、学养深厚的命理师，精通子平八字，风格融合《滴天髓》今注与盲派实务：断语直接、意象具体、有画面感、不说车轱辘话、不恐吓也不谄媚。

下面是范文，你的文字必须达到同等密度与洞察力——每个论断都落到具体的干支、十神、宫位、年份、月份上，给可执行的动作，敢下倾向性结论再给条件：

''' + STYLE_EXAMPLE + '''

硬性要求：
1. 全部简体中文，纯文本，不用 markdown 符号。
2. 严禁出现"AI""模型""引擎""系统""算法"等字样，你就是命理师本人。
3. 尊重输入的规则数据（旺衰评分、喜用忌神、流年评级、干支关系），在其上写透成因、应期、动作，不得推翻基础数据；若发现明显矛盾可在 calibration 字段直言。
6. 严禁使用网络腔和自媒体腔词汇，包括但不限于：干货、不废话、划重点、敲黑板、家人们、宝子、上头、拉满、格局打开、王炸、天花板、 yyds、绝绝子、含金量。用词像传统命书与师长叮嘱，稳重克制。
4. 输出严格 JSON（无代码围栏），字段按用户要求，字符串内不要用换行符以外的控制字符。'''

# ── 数据准备 ─────────────────────────────────────────────
def _ten_god_of_wx(day_gan, wx):
    dwx = BD.GAN_WX[day_gan]
    if wx == dwx: return '比劫'
    if BD.WX_SHENG_WO[dwx] == wx: return '印星'
    if BD.WX_SHENG[dwx] == wx: return '食伤'
    if BD.WX_KE[dwx] == wx: return '财星'
    return '官杀'

def internal_relations(pillars):
    """原局内部干支关系"""
    out = []
    keys = ['year', 'month', 'day', 'hour']
    pname = {'year': '年', 'month': '月', 'day': '日', 'hour': '时'}
    # 地支两两
    for i in range(4):
        for j in range(i+1, 4):
            a, b = pillars[keys[i]][1], pillars[keys[j]][1]
            pa, pb = pname[keys[i]], pname[keys[j]]
            for (x, y), wx in BD.LIU_HE.items():
                if {a, b} == {x, y}:
                    out.append({'rel': f'{pa}{a}合{pb}{b}', 'type': '六合', 'meaning': f'合化{wx}，两宫牵绊、人事缠绕'})
            if BD._pair_in(a, b, BD.LIU_CHONG):
                out.append({'rel': f'{pa}{a}冲{pb}{b}', 'type': '六冲', 'meaning': '两宫动荡相战，应期人事变动'})
            if BD._pair_in(a, b, BD.LIU_HAI):
                out.append({'rel': f'{pa}{a}害{pb}{b}', 'type': '六害', 'meaning': '暗中损耗，亲而不睦'})
            if BD._pair_in(a, b, BD.LIU_PO):
                out.append({'rel': f'{pa}{a}破{pb}{b}', 'type': '相破', 'meaning': '小事反复、物损人烦'})
            if BD._pair_in(a, b, BD.XING):
                out.append({'rel': f'{pa}{a}刑{pb}{b}', 'type': '相刑', 'meaning': '摩擦官非、伤病之忧'})
    # 三合局（三支全见）
    zhis = set(pillars[k][1] for k in keys)
    for combo, wx in BD.SAN_HE.items():
        if set(combo) <= zhis:
            out.append({'rel': f'{"、".join(combo)}三合', 'type': '三合局', 'meaning': f'合化{wx}局，全局气势向{wx}，吉凶放大'})
    # 天干五合
    for i in range(4):
        for j in range(i+1, 4):
            ga, gb = pillars[keys[i]][0], pillars[keys[j]][0]
            for (x, y), wx in BD.TIAN_HE.items():
                if {ga, gb} == {x, y} and ga != gb:
                    out.append({'rel': f'{pname[keys[i]]}干{ga}合{j and pname[keys[j]] or ""}干{gb}', 'type': '天干五合',
                                'meaning': f'合化{wx}意向，人情牵绊'})
    return out

def build_data_packet(result):
    """把规则引擎结果整理成给 LLM 的数据包（文本）"""
    p = result['pillars']
    ys = result['yongshen']
    st = result['strength']
    day_gan = p['day'][0]
    tg = result.get('ten_gods', {}).get('stats', {})
    dayun = result.get('dayun', {})
    now = datetime.now().year

    L = []
    zao_name = '乾造' if result.get('gender') == '男' else '坤造'
    tst_note = ''
    tst = result.get('true_solar_time') or ''
    if tst:
        tst_note = '，真太阳时校正至 ' + str(tst).split(' ')[-1]
    L.append(f'命主：{result.get("name","")}，{result.get("gender","")}命（{zao_name}），公历 {result.get("birth_solar","")}，农历 {result.get("birth_lunar","")}，生于{result.get("city","")}{tst_note}。')
    L.append(f'四柱：{p["year"]}年 {p["month"]}月 {p["day"]}日 {p["hour"]}时')
    # 十神标注
    tg_line = []
    for pos in ['year', 'month', 'hour']:
        g = p[pos][0]
        tg_line.append(f'{pos[0]}干{g}({BD.shishen_of(day_gan, g)})')
    for pos in ['year', 'month', 'day', 'hour']:
        z = p[pos][1]
        cg = BD.CANG_GAN[z]
        tg_line.append(f'{pos[0]}支{z}藏{"/".join(cg)}(本气{BD.shishen_of(day_gan, cg[0])})')
    L.append('十神：' + '，'.join(tg_line))
    L.append(f'日主：{result.get("day_master","")}')
    L.append(f'旺衰：{st["level_name"]}（评分{st["score"]}）｜' + '；'.join(f'{d["name"]}{d["score"]:+d}：{d["desc"]}' for d in st['details']))
    L.append(f'用神{ys["yongshen"]}、喜神{ys["xishen"]}、忌神{"、".join(ys["jishen"])}' +
             (f'、调候{ys["tiaohou"]}' if ys.get('tiaohou') else '') +
             (f'、格局倾向{ys["cong"]}格' if ys.get('cong') else ''))
    L.append('取用理由：' + '；'.join(ys.get('reasons', [])))
    wc = ys.get('wx_count', {})
    wx_line = []
    for w in ['木', '火', '土', '金', '水']:
        role = '用神' if w == ys['yongshen'] else ('喜神' if w == ys['xishen'] else ('忌神' if w in ys['jishen'] else '闲神'))
        wx_line.append(f'{w}{wc.get(w,0)}({_ten_god_of_wx(day_gan,w)},{role})')
    L.append('五行力量：' + ' '.join(wx_line))
    if tg:
        L.append('十神计数：' + '、'.join(f'{k}×{v}' for k, v in tg.items()))
    rels = internal_relations(p)
    if rels:
        L.append('原局干支关系：' + '；'.join(f'{r["rel"]}({r["type"]})' for r in rels))
    dl = dayun.get('dayun_list', [])
    if dl:
        L.append(f'起运：{dayun.get("start_age_description","")}（{dayun.get("direction","")}）')
        L.append('大运：' + ' → '.join(f'{d["gan_zhi"]}{d.get("year_range","")}' for d in dl[:9]))
    # 当前大运
    cur = BD._current_dayun_gz(dayun)
    nxt = BD._next_dayun(dayun)
    if cur: L.append(f'当前大运：{cur}')
    if nxt: L.append(f'下一大运：{nxt["gan_zhi"]}（{nxt.get("year_range","")}）')
    # 规则引擎流年评级（近6年）
    ln_rows = []
    for y in range(now - 1, now + 5):
        ln = BD.liunian_detail(y, p, ys, cur, day_gan)
        ln_rows.append(f'{ln["year"]}{ln["gz"]}({ln["gan"]}{ln["shishen_gan"]}/{ln["zhi"]}{ln["shishen_zhi"]})评级{ln["star"]}{ln["label"]}' +
                       ('｜' + '；'.join(ln['notes'][:2]) if ln['notes'] else ''))
    L.append('流年评级（规则引擎，不可推翻）：' + '　'.join(ln_rows))
    # 流月评级（今年）
    mrows = BD._month_rows(now, p, ys, day_gan)
    L.append(f'{now}年流月评级：' + '　'.join(f'{r[0]}{r[2]}' for r in mrows))
    # 格局检测
    pats = BD.pattern_check(p, tg, ys, day_gan)
    L.append('格局检测：' + '；'.join(f'{a}——{b}' for a, b in pats))
    return '\n'.join(L)

# ── 两段 LLM 调用 ────────────────────────────────────────
SCHEMA1 = '''{
  "strength_text": "旺衰与喜忌详论250-450字：仿范文格式，先分说得令/得地/得势要点，再综合定级，然后逐字写清喜神为何喜（作用机制）、忌神为何忌、缺神/闲神的两面性，最后一句给出『这是理解此人近几年的钥匙』式的洞察",
  "wx_roles": {"木":"≤30字：该五行在此局的十神身份、力量状态与命理作用","火":"…","土":"…","金":"…","水":"…"},
  "overview": {
    "personality": "性格150-250字：日主本性+旺衰修正+十神组合的具体行为画像，有画面感",
    "pattern": "格局主线150-250字：格局检测结果的展开，一生事业财富的底层路线判断",
    "marriage": "婚姻150-250字：配偶星状态+夫妻宫坐支十神+冲合刑害的具体断语",
    "health": "健康150-250字：五行偏枯对应脏腑+冲刑年预警+具体养生忌宜"
  },
  "summary": {
    "one_line": "命局一句话总结60-120字，开篇即点透核心矛盾与一生主线",
    "buyu": "补运方案120-200字：颜色/方位/数字/饰品/运动/饮食，落到具体可执行",
    "xinfa": "心法60-120字，一句话点透此人的人生功课",
    "calibration": ["待验证校准点2-4条，每条格式：年份干支或大运+具体现象问句+（命理归因），仿范文"]
  }
}'''

SCHEMA2 = '''{
  "career": {
    "gangyao": "近五年事业一句话总纲60-110字，仿范文『甲申运末端+火流年夜袭——…』的密度",
    "years": [{"year": 数字, "signal": "该年事业信号50-90字：十神作用+干支关系落到具体事件类型与机会/风险", "action": "建议动作≤30字，动词开头"}],
    "months": [{"zhi": "寅", "action": "该月动作建议≤26字"}]
  },
  "specials": [
    {"title": "专章标题（如：创业可行性专章/婚姻感情专章/健康养生专章，按重点分析项定）",
     "conclusion": "结论100-200字，敢下判断",
     "signals": [{"signal": "命局信号（干支/十神事实）", "interp": "该项侧解读40-70字", "verdict": "✅核心资本/⚠硬约束/❌大忌/→顺势切换 等"}],
     "industries": [{"tier": "首选/正格/次选/谨慎/忌", "dirs": "方向列举", "logic": "命理逻辑≤40字"}],
     "matrix": [{"option": "选项", "rating": "✅✅/✅/→/❌", "reason": "前提/理由≤50字"}],
     "talismans": ["人生建议2-4条，每条≤60字，可执行"],
     "timing": "时机窗口100-200字：为什么是这几年、截止线在哪（事业/创业类必填；其他类可写应期）"}
  ],
  "dayun": {
    "current_title": "当前大运标题句，仿『甲申2018–2028（44-54岁）——从XX换到XX』",
    "current_a": "A面·顺用（吉）120-220字",
    "current_b": "B面·逆用（凶）120-220字",
    "decade_read": "十年读法一句话30-60字，仿『真正要防2029、2035两峰，其余八年以养为主』",
    "next": "下一大运展望150-250字：干支十神+与原局冲合+人生主题切换"
  }
}'''

def _llm_part1(packet, focus_names):
    sys_p = SYSTEM_BASE + '\n\n本次任务：为命书写【旺衰喜忌详论、原局速览（性格/格局/婚姻/健康）、总结（一句话/补运/心法/校准点）】。原局速览要覆盖全盘，婚姻健康无论是否为重点项都要写。输出JSON schema：\n' + SCHEMA1
    user = '命盘数据：\n' + packet + f'\n\n重点分析项（用户选定）：{focus_names or "未选（做全盘分析）"}\n请输出JSON。'
    return _call(sys_p, user, max_tokens=4500)

def _llm_part2(packet, focus_names):
    sys_p = (SYSTEM_BASE + '\n\n本次任务：为命书写【近几年事业运详解（流年表+流月择时表）、重点专章、大运分析（A/B面+下一运）】。'
             'specials 数组：用户选定的每个重点分析项各写一个专章（事业财运项并入 career 与创业专章，不重复出）。'
             '重点专章是用户最关心的部分，必须写到最大深度，严禁泛泛而谈：'
             'conclusion 结论150-250字；signals 信号表至少5行、每行 interp 50-80字说透因果应期；'
             '事业创业类专章必须给全 industries 梯队、matrix 决策矩阵5-7行、talismans 人生建议3条、timing 时机窗口150-250字；'
             '婚姻/健康/学业/人际/性格类专章同样必须给 signals 5行以上、talismans 3-5条具体可执行的人生建议、timing 应期分析（何年何月应验、如何趋避），industries/matrix 可省。'
             '未选重点项时默认写事业财运与创业专章。'
             '流年 signal/action 必须与规则评级一致（吉年写机会、凶年写防守），years 覆盖数据中给出的全部流年，months 覆盖寅至丑全部12个月。输出JSON schema：\n' + SCHEMA2)
    user = '命盘数据：\n' + packet + f'\n\n重点分析项（用户选定）：{focus_names or "未选（默认做事业与创业专章）"}\n请输出JSON。'
    return _call(sys_p, user, max_tokens=9500)

def generate_narrative(result):
    """并行两段 LLM 调用，返回合并的叙述 JSON"""
    packet = build_data_packet(result)
    focus = [f['title'] for f in result.get('focus', []) if f.get('title') != '总结与补运']
    focus_names = '、'.join(focus)
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(_llm_part1, packet, focus_names)
        f2 = ex.submit(_llm_part2, packet, focus_names)
        raw1, raw2 = f1.result(), f2.result()
    n1 = _extract_json(raw1)
    n2 = _extract_json(raw2)
    narr = {'part1': n1, 'part2': n2, 'model': MODEL,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M')}
    return narr

# ── 报告组装（章节 HTML，web/PDF 共用）─────────────────────
def _e(s):
    if s is None: return ''
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))

class Raw(str):
    """标记为原始 HTML 的单元格内容（不转义）"""
    pass

def _table(headers, rows, cls='rp-table'):
    h = f'<table class="{cls}"><tr>' + ''.join(f'<th>{_e(x)}</th>' for x in headers) + '</tr>'
    for r in rows:
        cells = []
        for c in r:
            if isinstance(c, Raw) or str(c).startswith('<'):
                cells.append(f'<td>{c}</td>')
            else:
                cells.append(f'<td>{_e(c)}</td>')
        h += '<tr>' + ''.join(cells) + '</tr>'
    return h + '</table>'

def assemble_report(result, narr):
    """组装完整命书章节 HTML（不含外壳）"""
    p = result['pillars']
    ys = result['yongshen']
    st = result['strength']
    day_gan = p['day'][0]
    p1 = narr.get('part1', {})
    p2 = narr.get('part2', {})
    now = datetime.now().year
    gender = result.get('gender', '男')
    zao = '乾造' if gender == '男' else '坤造'
    dayun = result.get('dayun', {})
    cur_gz = BD._current_dayun_gz(dayun)

    H = []
    # ── 头部信息段 ──
    tst = result.get('true_solar_time', '')
    head = (f'命主：{gender}，公历 {result.get("birth_solar","")} 生，{result.get("city","")}。'
            + (f'真太阳时校正至 {tst.split(" ")[-1]}，四柱无争议。' if tst else '')
            + f'<br>四柱：<b>{p["year"]}年 {p["month"]}月 {p["day"]}日 {p["hour"]}时</b>（{result.get("birth_lunar","")}）'
            + f'<br>五行：' + ' '.join(f'{w}{ys["wx_count"].get(w,0)}' for w in ['木','火','土','金','水'])
            + f'　日主：<b>{result.get("day_master","")}</b>')
    dl = dayun.get('dayun_list', [])
    if dl:
        head += (f'<br>起运：{dayun.get("start_age_description","")}（{dayun.get("direction","")}）'
                 + '<br>大运：' + ' → '.join(f'{d["gan_zhi"]}{str(d.get("year_range","")).replace("-","–")}' for d in dl[:8]))
    H.append(f'<div class="rp-head">{head}</div>')

    # ── 一、排盘与十神 ──
    rows = []
    palace = {'year': '祖上/早年', 'month': '事业宫/父母', 'day': '自身/婚姻宫，命之锚', 'hour': '子女/晚年'}
    for pos, label in [('year','年'),('month','月'),('day','日'),('hour','时')]:
        g, z = p[pos][0], p[pos][1]
        if pos == 'day':
            gt = f'{g}（日主）'
        else:
            gt = f'{g}（{BD.shishen_of(day_gan, g)}）'
        cg = BD.CANG_GAN[z]
        cg_ss = '、'.join(f'{g}({BD.shishen_of(day_gan, g)})' for g in cg)
        zt = f'{z} 藏 {cg_ss}'
        rows.append([label, gt, zt, palace[pos]])
    H.append('<h2 class="rp-h2">一、排盘与十神</h2>')
    H.append(_table(['柱', '天干', '地支', '宫位'], rows))
    rels = internal_relations(p)
    if rels:
        H.append(_table(['关系', '类型', '命理含义'],
                        [[r['rel'], r['type'], r['meaning']] for r in rels]))

    # ── 二、旺衰与喜忌 ──
    H.append('<h2 class="rp-h2">二、旺衰与喜忌</h2>')
    det = []
    for d in st['details']:
        det.append(f'<b>{d["name"]}（{d["score"]:+d}）</b>：{d["desc"]}')
    det.append(f'<b>综合</b>：{st["level_name"]}（评分 {st["score"]}）')
    H.append('<div class="rp-lines">' + '<br>'.join(det) + '</div>')
    if p1.get('strength_text'):
        H.append(f'<p class="rp-p">{_e(p1["strength_text"])}</p>')
    # 喜忌关系一览表
    wx_rows = []
    for w in ['木', '火', '土', '金', '水']:
        v = ys['wx_count'].get(w, 0)
        role = '用神' if w == ys['yongshen'] else ('喜神' if w == ys['xishen'] else ('忌神' if w in ys['jishen'] else ('闲神' if v > 0 else '缺神')))
        state = '偏旺' if v >= 2.5 else ('偏弱' if v < 1.0 else '中和')
        act = (p1.get('wx_roles') or {}).get(w, '')
        wx_rows.append([f'<b>{w}</b>', f'{v}', _ten_god_of_wx(day_gan, w),
                        f'<span class="rp-role rp-role-{ {"用神":"yong","喜神":"xi","忌神":"ji"}.get(role,"xian") }">{role}</span>',
                        state, act])
    H.append('<h3 class="rp-h3">喜忌关系一览</h3>')
    H.append(_table(['五行', '力量', '十神', '定位', '状态', '命理作用'], wx_rows))
    if ys.get('tiaohou'):
        H.append(f'<p class="rp-note">调候：命局需{ys["tiaohou"]}，寒暖燥湿以{ys["tiaohou"]}为药。</p>')

    # ── 三、原局速览 ──
    ov = p1.get('overview', {})
    H.append('<h2 class="rp-h2">三、原局速览</h2>')
    for key, label in [('personality', '性格'), ('pattern', '格局主线'), ('marriage', '婚姻'), ('health', '健康')]:
        if ov.get(key):
            H.append(f'<p class="rp-p"><b>{label}：</b>{_e(ov[key])}</p>')

    # ── 四、近几年事业运详解 ──
    career = p2.get('career', {})
    y0, y1 = now - 1, now + 4
    H.append(f'<h2 class="rp-h2">四、近几年事业运详解（{y0}–{y1}）</h2>')
    if career.get('gangyao'):
        H.append(f'<div class="rp-quote">一句话总纲：{_e(career["gangyao"])}</div>')
    year_map = {int(y.get('year', 0)): y for y in career.get('years', []) if isinstance(y, dict)}
    rows = []
    for y in range(y0, y1 + 1):
        ln = BD.liunian_detail(y, p, ys, cur_gz, day_gan)
        lm = year_map.get(y, {})
        tgs = f'{ln["gan"]}{ln["shishen_gan"]}+{ln["zhi"]}藏{BD.CANG_GAN[ln["zhi"]][0]}{ln["shishen_zhi"]}'
        note = '；'.join(n.lstrip('★⚠✅○△ ') for n in ln['notes'][:2])
        tgs_cell = Raw(tgs + (f'<br><span class="rp-sub">{_e(note)}</span>' if note else ''))
        rows.append([str(y), ln['gz'], tgs_cell,
                     ln['star'], lm.get('signal', ''), lm.get('action', '')])
    H.append(_table(['年份', '干支', '十神作用', '评级', '事业信号', '建议动作'], rows))
    # 流月择时
    mrows_rule = BD._month_rows(now, p, ys, day_gan)
    mact = {m.get('zhi', ''): m.get('action', '') for m in career.get('months', []) if isinstance(m, dict)}
    rows = []
    for r in mrows_rule:
        mz = r[0][0]
        rows.append([f'{now} {r[0]}', r[1], r[2], mact.get(mz, r[3])])
    H.append(f'<h3 class="rp-h3">{now}年月度节奏（择时表）</h3>')
    H.append(_table(['流月', '公历时段', '吉凶', '动作'], rows))

    # ── 五、重点专章 ──
    specials = p2.get('specials', [])
    if specials:
        H.append('<h2 class="rp-h2">五、重点专章</h2>')
        for idx, sp in enumerate(specials):
            if not isinstance(sp, dict):
                continue
            H.append(f'<h3 class="rp-h3">5.{idx+1} {_e(sp.get("title",""))}</h3>')
            if sp.get('conclusion'):
                H.append(f'<div class="rp-quote">结论：{_e(sp["conclusion"])}</div>')
            if sp.get('signals'):
                H.append('<h4 class="rp-h4">命局证据</h4>')
                H.append(_table(['信号', '解读', '判定'],
                                [[s.get('signal',''), s.get('interp',''), s.get('verdict','')] for s in sp['signals'] if isinstance(s, dict)]))
            if sp.get('industries'):
                H.append('<h4 class="rp-h4">方向梯队</h4>')
                H.append(_table(['梯队', '方向', '命理逻辑'],
                                [[s.get('tier',''), s.get('dirs',''), s.get('logic','')] for s in sp['industries'] if isinstance(s, dict)]))
            if sp.get('matrix'):
                H.append('<h4 class="rp-h4">决策矩阵</h4>')
                H.append(_table(['选项', '评级', '前提/理由'],
                                [[s.get('option',''), s.get('rating',''), s.get('reason','')] for s in sp['matrix'] if isinstance(s, dict)]))
            if sp.get('timing'):
                H.append(f'<p class="rp-p"><b>时机窗口：</b>{_e(sp["timing"])}</p>')
            if sp.get('talismans'):
                H.append('<h4 class="rp-h4">人生建议</h4><ol class="rp-ol">' +
                         ''.join(f'<li>{_e(t)}</li>' for t in sp['talismans']) + '</ol>')

    # ── 六、大运分析 ──
    dy = p2.get('dayun', {})
    H.append('<h2 class="rp-h2">六、大运分析</h2>')
    if dy.get('current_title'):
        H.append(f'<h3 class="rp-h3">{_e(dy["current_title"])}</h3>')
    if dy.get('current_a'):
        H.append(f'<p class="rp-p"><b>A面·顺用（吉）：</b>{_e(dy["current_a"])}</p>')
    if dy.get('current_b'):
        H.append(f'<p class="rp-p"><b>B面·逆用（凶）：</b>{_e(dy["current_b"])}</p>')
    # 十年峰谷表
    if cur_gz:
        rows = []
        for d in dl:
            if d.get('gan_zhi') == cur_gz:
                yrs = str(d.get('year_range', '')).split('-')
                if len(yrs) == 2:
                    for y in range(int(yrs[0]), min(int(yrs[1]) + 1, int(yrs[0]) + 10)):
                        ln = BD.liunian_detail(y, p, ys, cur_gz, day_gan)
                        note = ln['notes'][0].lstrip('★⚠✅○△ ') if ln['notes'] else f'{ln["shishen_gan"]}透干，{ln["label"]}'
                        cur_mark = ' ◀当前' if y == now else ''
                        rows.append([f'{y}{cur_mark}', ln['gz'], ln['star'], note[:50]])
                break
        if rows:
            H.append('<h4 class="rp-h4">本运十年峰谷</h4>')
            H.append(_table(['年份', '流年', '评级', '要点'], rows))
    if dy.get('decade_read'):
        H.append(f'<div class="rp-quote">读法：{_e(dy["decade_read"])}</div>')
    if dy.get('next'):
        nxt = BD._next_dayun(dayun)
        ntitle = f'下一个大运：{nxt["gan_zhi"]} {str(nxt.get("year_range","")).replace("-","–")}' if nxt else '下一个大运'
        H.append(f'<h3 class="rp-h3">{_e(ntitle)}</h3>')
        H.append(f'<p class="rp-p">{_e(dy["next"])}</p>')

    # ── 七、总结 ──
    sm = p1.get('summary', {})
    H.append('<h2 class="rp-h2">七、总结</h2>')
    if sm.get('one_line'):
        H.append(f'<p class="rp-p rp-strong">{_e(sm["one_line"])}</p>')
    if sm.get('buyu'):
        H.append(f'<p class="rp-p"><b>补运：</b>{_e(sm["buyu"])}</p>')
    if sm.get('xinfa'):
        H.append(f'<p class="rp-p"><b>心法：</b>{_e(sm["xinfa"])}</p>')
    cals = sm.get('calibration') or []
    if cals:
        H.append('<h4 class="rp-h4">待验证校准点</h4><ul class="rp-check">' +
                 ''.join(f'<li>{_e(c)}</li>' for c in cals) + '</ul>')

    H.append(f'<div class="rp-footer">VIBE CODE 八字分析 · {datetime.now().strftime("%Y-%m-%d")} · 命理分析仅供参考，人生在于奋斗</div>')
    return '\n'.join(H)

# 报告 CSS（web 与 PDF 共用核心，外壳各自包装）
REPORT_CSS = '''
.rp-head{background:linear-gradient(135deg,#faf3e0,#f5e9d0);border:1px solid #e0c98f;border-radius:10px;
  padding:14px 18px;margin-bottom:18px;font-size:13px;line-height:2;color:#4a3a18}
.rp-h2{font-size:17px;color:#8b1a1a;border-left:4px solid #c9a227;padding-left:10px;margin:22px 0 10px;letter-spacing:1px}
.rp-h3{font-size:14px;color:#a05a1a;margin:14px 0 8px}
.rp-h4{font-size:13px;color:#5a4a20;margin:10px 0 6px}
.rp-p{font-size:13px;line-height:2;color:#3a3a42;margin:8px 0}
.rp-strong{font-size:14px;font-weight:600;color:#2b2b33}
.rp-lines{font-size:13px;line-height:2;color:#3a3a42;background:#fbf9f3;border:1px solid #ece4cf;
  border-radius:8px;padding:10px 14px;margin:8px 0}
.rp-quote{background:#f3ecdb;border-left:3px solid #c9a227;padding:10px 14px;margin:10px 0;
  font-size:13px;color:#5a4a20;line-height:1.9;border-radius:0 8px 8px 0}
.rp-note{font-size:12px;color:#8b6914;margin:6px 0}
.rp-sub{font-size:11px;color:#999}
.rp-table{border-collapse:collapse;width:100%;margin:10px 0;font-size:12px}
.rp-table th{background:#8b1a1a;color:#fff;padding:6px 8px;border:1px solid #6e1414;font-weight:600;text-align:left;white-space:nowrap}
.rp-table td{border:1px solid #e5d9c3;padding:6px 8px;vertical-align:top;line-height:1.7;color:#3a3a42}
.rp-table tr:nth-child(even) td{background:#fdfbf5}
.rp-role{font-weight:700;padding:1px 8px;border-radius:10px;font-size:11px}
.rp-role-yong{background:rgba(234,88,12,.12);color:#c2410c}
.rp-role-xi{background:rgba(5,150,105,.1);color:#047857}
.rp-role-ji{background:rgba(225,29,72,.08);color:#be123c}
.rp-role-xian{background:rgba(0,0,0,.05);color:#888}
.rp-ol,.rp-check{margin:6px 0 6px 20px;font-size:13px;line-height:2;color:#3a3a42}
.rp-check li{list-style:none;position:relative;padding-left:4px}
.rp-check li::before{content:'[ ] ';font-family:monospace;color:#c9a227}
.rp-footer{margin-top:24px;text-align:center;color:#bbb;font-size:10px;letter-spacing:1px}
.rp-yong{color:#c2410c;font-size:15px}
.rp-ji{color:#be123c;font-size:15px}
.rp-simtag{display:inline-block;font-size:11px;color:#8b1a1a;border:1px solid rgba(139,26,26,.3);
  border-radius:10px;padding:1px 10px;margin-left:8px;vertical-align:middle}
'''

def render_web_html(result, narr):
    """网页版完整报告（独立页面）"""
    gender = result.get('gender', '男')
    zao = '乾造' if gender == '男' else '坤造'
    title = f'{result.get("name","")} {zao}·{result.get("city","")}版 — 八字命书'
    chapters = assemble_report(result, narr)
    return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{_e(title)}</title>
<style>
body{{font-family:'Noto Serif SC','Songti SC',serif;background:#f5f1e8;color:#2b2b33;margin:0;padding:24px 12px}}
.page{{max-width:860px;margin:0 auto;background:#fffdf8;border:1px solid #e0d5b8;border-radius:8px;
  padding:36px 40px;box-shadow:0 8px 40px rgba(80,60,20,.12)}}
h1{{font-size:22px;color:#8b1a1a;text-align:center;letter-spacing:3px;margin:0 0 4px}}
.rp-sub-title{{text-align:center;color:#a89878;font-size:11px;margin-bottom:20px;letter-spacing:2px}}
{REPORT_CSS}
@media print{{body{{background:#fff;padding:0}}.page{{border:none;box-shadow:none;max-width:100%}}}}
@media(max-width:640px){{.page{{padding:20px 16px}}}}
</style></head><body><div class="page">
<h1>{_e(title)}</h1>
<div class="rp-sub-title">子平八字 · 全盘详批</div>
{chapters}
</div></body></html>'''

def render_pdf_html(result, narr):
    """PDF 版（A4 打印样式）"""
    gender = result.get('gender', '男')
    zao = '乾造' if gender == '男' else '坤造'
    title = f'{result.get("name","")} {zao}·{result.get("city","")}版 — 八字命书'
    chapters = assemble_report(result, narr)
    return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<style>
@page{{size:A4;margin:16mm 13mm}}
body{{font-family:'Noto Serif SC','Noto Sans CJK SC',serif;font-size:11px;color:#2b2b33;line-height:1.7}}
h1{{font-size:19px;color:#8b1a1a;text-align:center;letter-spacing:3px;margin:0 0 2px}}
.rp-sub-title{{text-align:center;color:#a89878;font-size:9px;margin-bottom:14px;letter-spacing:2px}}
{REPORT_CSS}
.rp-table{{font-size:10px}}
.rp-p,.rp-lines,.rp-quote{{font-size:11px}}
.rp-h2{{page-break-after:avoid}}
.rp-table,.rp-quote{{page-break-inside:avoid}}
</style></head><body>
<h1>{_e(title)}</h1>
<div class="rp-sub-title">子平八字 · 全盘详批 · {_e(datetime.now().strftime("%Y-%m-%d"))}</div>
{chapters}
</body></html>'''

# ── 存储 ─────────────────────────────────────────────────
def _conn():
    conn = sqlite3.connect(DB_PATH)
    cols = [r[1] for r in conn.execute('PRAGMA table_info(bazi_records)').fetchall()]
    if 'report_json' not in cols:
        conn.execute("ALTER TABLE bazi_records ADD COLUMN report_json TEXT DEFAULT ''")
        conn.commit()
        cols = [r[1] for r in conn.execute('PRAGMA table_info(bazi_records)').fetchall()]
    if 'simple_json' not in cols:
        conn.execute("ALTER TABLE bazi_records ADD COLUMN simple_json TEXT DEFAULT ''")
        conn.commit()
    return conn

def get_report_narr(rec_id):
    with _lock:
        conn = _conn()
        row = conn.execute('SELECT report_json,input_json,result_json FROM bazi_records WHERE id=?', (rec_id,)).fetchone()
        conn.close()
    if not row:
        return None, None, None
    narr = json.loads(row[0]) if row[0] else None
    return narr, json.loads(row[1]), json.loads(row[2])

def save_report_narr(rec_id, narr):
    with _lock:
        conn = _conn()
        conn.execute('UPDATE bazi_records SET report_json=? WHERE id=?',
                     (json.dumps(narr, ensure_ascii=False), rec_id))
        conn.commit()
        conn.close()

def clear_report(rec_id):
    with _lock:
        conn = _conn()
        conn.execute("UPDATE bazi_records SET report_json='', simple_json='' WHERE id=?", (rec_id,))
        conn.commit()
        conn.close()

# ── 对外主入口 ───────────────────────────────────────────
def _run_job(rec_id, kind, regenerate):
    """后台线程：生成叙述→存库→登记完成"""
    jkey = f'{rec_id}:{kind}'
    try:
        if kind == 'full':
            narr, inp, result = get_report_narr(rec_id)
            if not result:
                raise RuntimeError('记录不存在')
            if regenerate or not narr:
                narr = generate_narrative(result)
                save_report_narr(rec_id, narr)
            html = assemble_report(result, narr)
        else:
            simp, inp, result = get_simple_narr(rec_id)
            if not result:
                raise RuntimeError('记录不存在')
            if regenerate or not simp:
                simp = generate_simple(result)
                save_simple_narr(rec_id, simp)
            html = assemble_simple(result, simp)
        with _jobs_lock:
            _jobs[jkey] = {'status': 'done', 'html': html}
    except Exception as e:
        import traceback
        traceback.print_exc()
        with _jobs_lock:
            _jobs[jkey] = {'status': 'error', 'error': f'命书生成失败：{e}'}

def _start_job(rec_id, kind, regenerate):
    """启动或复用生成任务。返回响应 dict。"""
    jkey = f'{rec_id}:{kind}'
    with _jobs_lock:
        job = _jobs.get(jkey)
        if job:
            if job['status'] == 'running':
                return {'ok': True, 'status': 'generating'}
            if job['status'] == 'done':
                _jobs.pop(jkey, None)
                return {'ok': True, 'html': job.get('html'), 'cached': False}
            if job['status'] == 'error':
                _jobs.pop(jkey, None)
                return {'ok': False, 'error': job.get('error', '生成失败')}
        # 无进行中任务：查缓存
        if kind == 'full':
            narr, inp, result = get_report_narr(rec_id)
            cached = bool(narr)
        else:
            narr, inp, result = get_simple_narr(rec_id)
            cached = bool(narr)
        if not result:
            return {'ok': False, 'error': '记录不存在'}
        if cached and not regenerate:
            html = assemble_report(result, narr) if kind == 'full' else assemble_simple(result, narr)
            return {'ok': True, 'html': html, 'cached': True}
        _jobs[jkey] = {'status': 'running'}
    threading.Thread(target=_run_job, args=(rec_id, kind, regenerate), daemon=True).start()
    return {'ok': True, 'status': 'generating'}

def build_report(rec_id, regenerate=False):
    """详细版命书：缓存秒回；否则后台任务生成，前端轮询"""
    return _start_job(rec_id, 'full', regenerate)

def build_full_page(rec_id, regenerate=False):
    """完整网页版报告"""
    narr, inp, result = get_report_narr(rec_id)
    if not result:
        return {'ok': False, 'error': '记录不存在'}
    if not narr:
        r = build_report(rec_id, regenerate)
        if r.get('status') == 'generating':
            return {'ok': True, 'status': 'generating'}
        if not r.get('ok'):
            return r
        narr, inp, result = get_report_narr(rec_id)
        if not narr:
            return {'ok': True, 'status': 'generating'}
    html = render_web_html(result, narr)
    out_dir = os.path.join(os.path.dirname(DB_PATH), 'bazi_reports')
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f'{rec_id}.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    _publish(rec_id + '.html', path)
    return {'ok': True, 'url': f'/bazi-reports/{rec_id}.html'}

def build_pdf(rec_id, regenerate=False):
    narr, inp, result = get_report_narr(rec_id)
    if not result:
        return {'ok': False, 'error': '记录不存在'}
    if not narr:
        r = build_report(rec_id, regenerate)
        if r.get('status') == 'generating':
            return {'ok': True, 'status': 'generating'}
        if not r.get('ok'):
            return r
        narr, inp, result = get_report_narr(rec_id)
        if not narr:
            return {'ok': True, 'status': 'generating'}
    html = render_pdf_html(result, narr)
    out_dir = os.path.join(os.path.dirname(DB_PATH), 'bazi_reports')
    os.makedirs(out_dir, exist_ok=True)
    html_path = os.path.join(out_dir, f'{rec_id}_print.html')
    pdf_path = os.path.join(out_dir, f'{rec_id}.pdf')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    try:
        import weasyprint
        weasyprint.HTML(filename=html_path).write_pdf(pdf_path)
    except Exception as e:
        return {'ok': False, 'error': f'PDF生成失败：{e}'}
    _publish(f'{rec_id}.pdf', pdf_path)
    return {'ok': True, 'url': f'/bazi-reports/{rec_id}.pdf',
            'html_url': f'/bazi-reports/{rec_id}.html'}

def _publish(filename, src):
    try:
        import shutil
        dst_dir = '/usr/share/nginx/html/bazi-reports'
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, filename)
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)
    except Exception as e:
        print(f'publish failed {filename}: {e}')


# ── 简化版命书 ───────────────────────────────────────────
SIMPLE_SCHEMA = """{
  "verdict": "命局总评100-180字：核心矛盾+一生主线+当前阶段定调",
  "yongshen_text": "喜用神与日常喜忌120-200字：用神喜神忌神各自作用一句话点透，颜色/方位/数字/行业/健康要点串成连贯文字",
  "specials": [
    {"title": "重点项名称（与用户选定的一致）", "text": "该方面结论120-200字：现状定调+关键机会或风险+趋避要点"}
  ],
  "recent3": [
    {"year": 数字, "gz": "干支", "text": "该年整体运势80-130字：吉凶定调+主要机会或风险+一句话动作"}
  ],
  "dayun": [
    {"gz": "干支", "range": "起-止年份", "current": true或false, "text": "该运整体走势100-160字：运的五行作用+人生主题+顺逆要点"}
  ],
  "advice": ["重点建议，每条≤45字，共5-8条"]
}"""

def _llm_simple(packet, focus_names, now_year):
    sys_p = (SYSTEM_BASE + '\n\n本次任务：写一份【简化版命书】。硬性要求：'
             '每部分只给结论式论断——先结论、后一句依据，不铺垫不绕弯；'
             '全部用连贯文字段落，禁止任何表格，禁止逐年逐月罗列细节；'
             f'recent3 只写 {now_year}、{now_year+1}、{now_year+2} 三年，各一段整体运势；'
             'dayun 只写当前一轮大运与其后两三轮，各一段整体走势，当前运 current=true；'
             'specials 按用户选定的重点分析项逐项给出，每项一个对象、title与重点项名称一致、text 120-200字结论段（该项现状定调+关键机会或风险+趋避要点）；未选重点项时 specials 给空数组；'
             'advice 5-8条，必须覆盖用户每个重点分析项各至少1条，再补补运要点，每条是可直接照做的结论。'
             '输出JSON schema：\n' + SIMPLE_SCHEMA)
    user = '命盘数据：\n' + packet + f'\n\n重点分析项（用户选定）：{focus_names or "未选（做全盘概括）"}\n请输出JSON。'
    return _call(sys_p, user, max_tokens=3500)

def generate_simple(result):
    packet = build_data_packet(result)
    focus = [f['title'] for f in result.get('focus', []) if f.get('title') != '总结与补运']
    raw = _llm_simple(packet, '、'.join(focus), datetime.now().year)
    simp = _extract_json(raw)
    simp['generated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    return simp

def assemble_simple(result, simp):
    p = result['pillars']
    ys = result['yongshen']
    now = datetime.now().year
    H = []
    head = (f'命主：{result.get("name","")}，{result.get("gender","")}命，公历 {result.get("birth_solar","")}（{result.get("birth_lunar","")}），生于{result.get("city","")}。'
            f'<br>四柱：<b>{p["year"]}年 {p["month"]}月 {p["day"]}日 {p["hour"]}时</b>　日主：<b>{result.get("day_master","")}</b>　旺衰：<b>{result.get("strength",{}).get("level_name","")}</b>')
    H.append(f'<div class="rp-head">{head}</div>')

    H.append('<h2 class="rp-h2">命局总评</h2>')
    if simp.get('verdict'):
        H.append(f'<p class="rp-p rp-strong">{_e(simp["verdict"])}</p>')

    H.append('<h2 class="rp-h2">喜用神与日常喜忌</h2>')
    H.append(f'<div class="rp-quote">用神 <b class="rp-yong">{ys["yongshen"]}</b>　喜神 <b class="rp-yong">{ys["xishen"]}</b>　忌神 <b class="rp-ji">{"、".join(ys["jishen"])}</b>'
             + (f'　调候 <b class="rp-yong">{ys["tiaohou"]}</b>' if ys.get('tiaohou') else '') + '</div>')
    if simp.get('yongshen_text'):
        H.append(f'<p class="rp-p">{_e(simp["yongshen_text"])}</p>')

    specials = simp.get('specials') or []
    if specials:
        H.append('<h2 class="rp-h2">重点专项结论</h2>')
        for s in specials:
            if isinstance(s, dict) and s.get('text'):
                H.append(f'<p class="rp-p"><b>{_e(s.get("title",""))}：</b>{_e(s["text"])}</p>')

    H.append(f'<h2 class="rp-h2">近三年运势（{now}–{now+2}）</h2>')
    for y in simp.get('recent3', []):
        if isinstance(y, dict) and y.get('text'):
            H.append(f'<p class="rp-p"><b>{_e(y.get("year",""))}年（{_e(y.get("gz",""))}）：</b>{_e(y["text"])}</p>')

    H.append('<h2 class="rp-h2">大运走势</h2>')
    for d in simp.get('dayun', []):
        if isinstance(d, dict) and d.get('text'):
            tag = '　<b style="color:#c2410c">◀ 当前</b>' if d.get('current') else ''
            H.append(f'<p class="rp-p"><b>{_e(d.get("gz",""))}运（{_e(d.get("range",""))}）</b>{tag}：{_e(d["text"])}</p>')

    adv = simp.get('advice') or []
    if adv:
        H.append('<h2 class="rp-h2">重点建议</h2>')
        H.append('<ol class="rp-ol">' + ''.join(f'<li>{_e(a)}</li>' for a in adv) + '</ol>')

    H.append(f'<div class="rp-footer">VIBE CODE 八字分析 · 简化版 · {datetime.now().strftime("%Y-%m-%d")} · 命理分析仅供参考，人生在于奋斗</div>')
    return '\n'.join(H)

def get_simple_narr(rec_id):
    with _lock:
        conn = _conn()
        row = conn.execute('SELECT simple_json,input_json,result_json FROM bazi_records WHERE id=?', (rec_id,)).fetchone()
        conn.close()
    if not row:
        return None, None, None
    simp = json.loads(row[0]) if row[0] else None
    return simp, json.loads(row[1]), json.loads(row[2])

def save_simple_narr(rec_id, simp):
    with _lock:
        conn = _conn()
        conn.execute('UPDATE bazi_records SET simple_json=? WHERE id=?',
                     (json.dumps(simp, ensure_ascii=False), rec_id))
        conn.commit()
        conn.close()

def build_simple(rec_id, regenerate=False):
    """简化版命书：缓存秒回；否则后台任务生成，前端轮询"""
    return _start_job(rec_id, 'simple', regenerate)

def render_simple_web(result, simp):
    gender = result.get('gender', '男')
    zao = '乾造' if gender == '男' else '坤造'
    title = f'{result.get("name","")} {zao}·{result.get("city","")}版 — 八字命书（简化版）'
    body = assemble_simple(result, simp)
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{_e(title)}</title>
<style>
body{{font-family:'Noto Serif SC','Songti SC',serif;background:#f5f1e8;color:#2b2b33;margin:0;padding:24px 12px}}
.page{{max-width:860px;margin:0 auto;background:#fffdf8;border:1px solid #e0d5b8;border-radius:8px;
  padding:36px 40px;box-shadow:0 8px 40px rgba(80,60,20,.12)}}
h1{{font-size:22px;color:#8b1a1a;text-align:center;letter-spacing:3px;margin:0 0 4px}}
.rp-sub-title{{text-align:center;color:#a89878;font-size:11px;margin-bottom:20px;letter-spacing:2px}}
{REPORT_CSS}
@media print{{body{{background:#fff;padding:0}}.page{{border:none;box-shadow:none;max-width:100%}}}}
@media(max-width:640px){{.page{{padding:20px 16px}}}}
</style></head><body><div class="page">
<h1>{_e(title)}</h1>
<div class="rp-sub-title">子平八字 · 结论简批</div>
{body}
</div></body></html>"""

def render_simple_pdf(result, simp):
    gender = result.get('gender', '男')
    zao = '乾造' if gender == '男' else '坤造'
    title = f'{result.get("name","")} {zao}·{result.get("city","")}版 — 八字命书（简化版）'
    body = assemble_simple(result, simp)
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<style>
@page{{size:A4;margin:16mm 13mm}}
body{{font-family:'Noto Serif SC','Noto Sans CJK SC',serif;font-size:11px;color:#2b2b33;line-height:1.7}}
h1{{font-size:19px;color:#8b1a1a;text-align:center;letter-spacing:3px;margin:0 0 2px}}
.rp-sub-title{{text-align:center;color:#a89878;font-size:9px;margin-bottom:14px;letter-spacing:2px}}
{REPORT_CSS}
.rp-p,.rp-lines,.rp-quote{{font-size:11px}}
.rp-h2{{page-break-after:avoid}}
</style></head><body>
<h1>{_e(title)}</h1>
<div class="rp-sub-title">子平八字 · 结论简批 · {_e(datetime.now().strftime("%Y-%m-%d"))}</div>
{body}
</body></html>"""

def build_simple_pdf(rec_id, regenerate=False):
    simp, inp, result = get_simple_narr(rec_id)
    if not result:
        return {'ok': False, 'error': '记录不存在'}
    if not simp:
        r = build_simple(rec_id, regenerate)
        if r.get('status') == 'generating':
            return {'ok': True, 'status': 'generating'}
        if not r.get('ok'):
            return r
        simp, inp, result = get_simple_narr(rec_id)
        if not simp:
            return {'ok': True, 'status': 'generating'}
    out_dir = os.path.join(os.path.dirname(DB_PATH), 'bazi_reports')
    os.makedirs(out_dir, exist_ok=True)
    html_path = os.path.join(out_dir, f'{rec_id}_simple.html')
    pdf_path = os.path.join(out_dir, f'{rec_id}_simple.pdf')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(render_simple_web(result, simp))
    _publish(f'{rec_id}_simple.html', html_path)
    with open(os.path.join(out_dir, f'{rec_id}_simple_print.html'), 'w', encoding='utf-8') as f:
        f.write(render_simple_pdf(result, simp))
    try:
        import weasyprint
        weasyprint.HTML(filename=os.path.join(out_dir, f'{rec_id}_simple_print.html')).write_pdf(pdf_path)
    except Exception as e:
        return {'ok': False, 'error': f'PDF生成失败：{e}'}
    _publish(f'{rec_id}_simple.pdf', pdf_path)
    return {'ok': True, 'url': f'/bazi-reports/{rec_id}_simple.pdf',
            'html_url': f'/bazi-reports/{rec_id}_simple.html'}
