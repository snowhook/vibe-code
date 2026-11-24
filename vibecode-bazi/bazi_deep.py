#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""八字深度分析引擎 — 参考专业命书结构
输出结构化 sections：text/list/table/kv/quote，供前端与PDF共用渲染。
"""
from datetime import datetime

GAN = '甲乙丙丁戊己庚辛壬癸'
ZHI_SEQ = '子丑寅卯辰巳午未申酉戌亥'
GAN_WX = {'甲': '木', '乙': '木', '丙': '火', '丁': '火', '戊': '土', '己': '土',
          '庚': '金', '辛': '金', '壬': '水', '癸': '水'}
ZHI_WX = {'子': '水', '丑': '土', '寅': '木', '卯': '木', '辰': '土', '巳': '火',
          '午': '火', '未': '土', '申': '金', '酉': '金', '戌': '土', '亥': '水'}
ALL_WX = dict(GAN_WX); ALL_WX.update(ZHI_WX)
GAN_YY = {g: ('阳' if i % 2 == 0 else '阴') for i, g in enumerate(GAN)}
CANG_GAN = {
    '子': ['癸'], '丑': ['己', '癸', '辛'], '寅': ['甲', '丙', '戊'], '卯': ['乙'],
    '辰': ['戊', '乙', '癸'], '巳': ['丙', '戊', '庚'], '午': ['丁', '己'],
    '未': ['己', '丁', '乙'], '申': ['庚', '壬', '戊'], '酉': ['辛'],
    '戌': ['戊', '辛', '丁'], '亥': ['壬', '甲'],
}
WX_SHENG = {'木': '火', '火': '土', '土': '金', '金': '水', '水': '木'}
WX_KE = {'木': '土', '土': '水', '水': '火', '火': '金', '金': '木'}
WX_SHENG_WO = {v: k for k, v in WX_SHENG.items()}
WX_KE_WO = {v: k for k, v in WX_KE.items()}

# ── 干支关系 ──
LIU_HE = {('子','丑'):'土',('寅','亥'):'木',('卯','戌'):'火',('辰','酉'):'金',('巳','申'):'水',('午','未'):'火土'}
LIU_CHONG = [('子','午'),('丑','未'),('寅','申'),('卯','酉'),('辰','戌'),('巳','亥')]
LIU_HAI = [('子','未'),('丑','午'),('寅','巳'),('卯','辰'),('申','亥'),('酉','戌')]
LIU_PO = [('子','酉'),('午','卯'),('巳','申'),('寅','亥'),('辰','丑'),('未','戌')]
SAN_HE = {'申子辰':'水','亥卯未':'木','寅午戌':'火','巳酉丑':'金'}
SAN_HUI = {'寅卯辰':'木','巳午未':'火','申酉戌':'金','亥子丑':'水'}
XING = [('寅','巳'),('巳','申'),('寅','申'),('丑','戌'),('戌','未'),('丑','未'),('子','卯')]
ZI_XING = ['辰','午','酉','亥']
TIAN_HE = {('甲','己'):'土',('乙','庚'):'金',('丙','辛'):'水',('丁','壬'):'木',('戊','癸'):'火'}
TIAN_CHONG = [('甲','庚'),('乙','辛'),('丙','壬'),('丁','癸')]

POS_NAME = {'year': '年柱(祖上/早年)', 'month': '月柱(事业/父母宫)', 'day': '日柱(自身/婚姻宫)', 'hour': '时柱(子女/晚年)'}

def _pair_in(a, b, pairs):
    for x, y in pairs:
        if (a == x and b == y) or (a == y and b == x):
            return True
    return False

def zhi_relations(z, pillars):
    """流年/运支 z 与原局四柱地支的关系"""
    rels = []
    chart_zhi = {k: pillars[k][1] for k in ['year', 'month', 'day', 'hour']}
    for pos, cz in chart_zhi.items():
        for (a, b), wx in LIU_HE.items():
            if (z == a and cz == b) or (z == b and cz == a):
                rels.append({'type': '合', 'pos': pos, 'zhi': cz, 'wx': wx,
                             'desc': f'与{POS_NAME[pos]}{cz}六合化{wx}'})
        if _pair_in(z, cz, LIU_CHONG):
            rels.append({'type': '冲', 'pos': pos, 'zhi': cz,
                         'desc': f'冲{POS_NAME[pos]}{cz}'})
        if _pair_in(z, cz, LIU_HAI):
            rels.append({'type': '害', 'pos': pos, 'zhi': cz,
                         'desc': f'与{POS_NAME[pos]}{cz}相害'})
        if _pair_in(z, cz, LIU_PO):
            rels.append({'type': '破', 'pos': pos, 'zhi': cz,
                         'desc': f'与{POS_NAME[pos]}{cz}相破'})
        if _pair_in(z, cz, XING):
            rels.append({'type': '刑', 'pos': pos, 'zhi': cz,
                         'desc': f'与{POS_NAME[pos]}{cz}相刑'})
        if z == cz and cz in ZI_XING:
            rels.append({'type': '刑', 'pos': pos, 'zhi': cz, 'desc': f'{cz}见{cz}自刑'})
    # 三合局：z + 原局两支
    all_zhi = set(chart_zhi.values())
    for combo, wx in SAN_HE.items():
        if z in combo:
            others = [x for x in combo if x != z]
            present = [o for o in others if o in all_zhi]
            if len(present) == 2:
                rels.append({'type': '三合', 'pos': 'global', 'zhi': ''.join(combo), 'wx': wx,
                             'desc': f'与原局{"、".join(present)}三合{wx}局'})
            elif len(present) == 1:
                rels.append({'type': '半合', 'pos': 'global', 'zhi': present[0], 'wx': wx,
                             'desc': f'与原局{present[0]}半合{wx}局'})
    return rels

def gan_relations(g, pillars):
    rels = []
    chart_gan = {'year': pillars['year'][0], 'month': pillars['month'][0], 'hour': pillars['hour'][0]}
    dg = pillars['day'][0]
    for (a, b), wx in TIAN_HE.items():
        if (g == a and dg == b) or (g == b and dg == a):
            rels.append({'type': '合身', 'desc': f'{g}与日主{dg}天干五合化{wx}'})
        for pos, cg in chart_gan.items():
            if cg == dg:
                continue
            if (g == a and cg == b) or (g == b and cg == a):
                rels.append({'type': '合', 'pos': pos, 'desc': f'与{pos[0]}干{cg}合化{wx}'})
    for a, b in TIAN_CHONG:
        if (g == a and dg == b) or (g == b and dg == a):
            rels.append({'type': '冲身', 'desc': f'{g}与日主{dg}天干相冲'})
    return rels

def shishen_of(day_gan, other_gan):
    dwx, dyy = GAN_WX[day_gan], GAN_YY[day_gan]
    owx, oyy = GAN_WX[other_gan], GAN_YY[other_gan]
    same = dyy == oyy
    if owx == dwx: return '比肩' if same else '劫财'
    if WX_SHENG_WO[dwx] == owx: return '偏印' if same else '正印'
    if WX_SHENG[dwx] == owx: return '食神' if same else '伤官'
    if WX_KE[dwx] == owx: return '偏财' if same else '正财'
    if WX_KE_WO[dwx] == owx: return '七杀' if same else '正官'
    return '?'

def year_ganzhi(y):
    g = GAN[(y - 4) % 10]
    z = ZHI_SEQ[(y - 4) % 12]
    return g + z

def month_gan(year_gan, month_zhi):
    """年上起月法"""
    start = {'甲': '丙', '己': '丙', '乙': '戊', '庚': '戊', '丙': '庚', '辛': '庚',
             '丁': '壬', '壬': '壬', '戊': '甲', '癸': '甲'}[year_gan]
    order = '寅卯辰巳午未申酉戌亥子丑'
    idx = order.index(month_zhi)
    return GAN[(GAN.index(start) + idx) % 10]

# 流月对应公历区间（近似节气）
MONTH_SOLAR = {'寅': '2月上旬-3月上旬', '卯': '3月上旬-4月上旬', '辰': '4月上旬-5月上旬',
               '巳': '5月上旬-6月上旬', '午': '6月上旬-7月上旬', '未': '7月上旬-8月上旬',
               '申': '8月上旬-9月上旬', '酉': '9月上旬-10月上旬', '戌': '10月上旬-11月上旬',
               '亥': '11月上旬-12月上旬', '子': '12月上旬-1月上旬', '丑': '1月上旬-2月上旬'}

def rate_relations(rels, ys):
    """按关系与喜忌打分，返回(score, notes)"""
    score = 0.0
    notes = []
    yong, xi, ji = ys['yongshen'], ys['xishen'], ys['jishen']
    for r in rels:
        t = r['type']
        pos_w = {'day': 1.2, 'month': 1.0, 'hour': 0.7, 'year': 0.5, 'global': 1.0}.get(r.get('pos', 'global'), 0.8)
        if t == '冲':
            s = -1.3 * pos_w
            cz_wx = ALL_WX[r['zhi']]
            if cz_wx in ji:
                s += 0.5   # 冲去忌神反有小吉
                notes.append(f"★{r['desc']}——冲去忌神{cz_wx}，动荡中有转机")
            elif cz_wx == yong or cz_wx == xi:
                s -= 0.6
                notes.append(f"⚠{r['desc']}——冲动喜用之根，防变动破耗")
            else:
                notes.append(f"⚠{r['desc']}——宫位动荡，人事变动")
            score += s
        elif t in ('刑',):
            score -= 0.9 * pos_w
            notes.append(f"⚠{r['desc']}——防官非伤病、关系摩擦")
        elif t == '害':
            score -= 0.5 * pos_w
            notes.append(f"△{r['desc']}——暗中损耗，防小人猜忌")
        elif t == '破':
            score -= 0.4 * pos_w
            notes.append(f"△{r['desc']}——小事反复，防财物损坏")
        elif t == '合':
            hewx = r.get('wx')
            cz_wx = ALL_WX[r['zhi']] if r.get('zhi') in ALL_WX else hewx
            if hewx == yong or hewx == xi:
                score += 1.0 * pos_w
                notes.append(f"✅{r['desc']}——合来喜用，人和事顺")
            elif cz_wx in ji:
                score += 0.6 * pos_w
                notes.append(f"○{r['desc']}——合绊忌神，压力暂缓")
            else:
                score += 0.2 * pos_w
                notes.append(f"○{r['desc']}——人缘牵绊，喜忧参半")
        elif t == '三合':
            if r['wx'] == yong or r['wx'] == xi:
                score += 1.6
                notes.append(f"✅{r['desc']}——喜用成局，气势大顺")
            elif r['wx'] in ji:
                score -= 1.6
                notes.append(f"⚠⚠{r['desc']}——忌神成局，其年宜守不宜攻")
            else:
                notes.append(f"○{r['desc']}")
        elif t == '半合':
            if r['wx'] == yong or r['wx'] == xi:
                score += 0.6
                notes.append(f"✅{r['desc']}")
            elif r['wx'] in ji:
                score -= 0.6
                notes.append(f"⚠{r['desc']}")
    return score, notes

def rate_element(wx, ys):
    if wx == ys['yongshen']: return 2.0
    if wx == ys['xishen']: return 1.2
    if wx in ys['jishen']: return -1.8
    return -0.2

def star_of(score):
    if score >= 3.2: return '✅✅', '大吉'
    if score >= 1.5: return '✅', '吉'
    if score >= -1.0: return '→', '平'
    if score >= -2.6: return '⚠', '小凶'
    return '⚠⚠', '凶'

def liunian_detail(year, pillars, ys, dayun_gz, day_gan):
    """单流年深度评级"""
    gz = year_ganzhi(year)
    g, z = gz[0], gz[1]
    sg = shishen_of(day_gan, g)
    sz = shishen_of(day_gan, CANG_GAN[z][0])
    score = rate_element(GAN_WX[g], ys) * 0.8 + rate_element(ALL_WX[z], ys) * 1.0
    notes = []
    zrels = zhi_relations(z, pillars)
    s1, n1 = rate_relations(zrels, ys)
    score += s1
    notes += n1
    grels = gan_relations(g, pillars)
    for r in grels:
        if r['type'] == '合身':
            score += 0.6
            notes.append(f"○{r['desc']}——人事牵绊、合作缠身")
        elif r['type'] == '冲身':
            score -= 0.6
            notes.append(f"⚠{r['desc']}——心神不宁、决策反复")
    # 岁运并临
    if dayun_gz and gz == dayun_gz:
        score *= 1.5
        notes.append('★岁运并临——吉凶倍增，变动之年')
    # 天克地冲日柱
    day_gz = pillars['day']
    if _pair_in(g, day_gz[0], TIAN_CHONG) and _pair_in(z, day_gz[1], LIU_CHONG):
        score -= 1.5
        notes.append('⚠⚠流年与日柱天克地冲——自身/婚姻宫大动荡，凡事求稳')
    star, label = star_of(score)
    return {'year': year, 'gz': gz, 'gan': g, 'zhi': z, 'shishen_gan': sg, 'shishen_zhi': sz,
            'score': round(score, 1), 'star': star, 'label': label, 'notes': notes}

# ── 深度分析块构建 ──
def _txt(c): return {'type': 'text', 'content': c}
def _list(items): return {'type': 'list', 'items': items}
def _table(headers, rows): return {'type': 'table', 'headers': headers, 'rows': rows}
def _quote(c): return {'type': 'quote', 'content': c}

def pattern_check(pillars, ten_stats, ys, day_gan):
    """格局主线检测"""
    patterns = []
    shang = ten_stats.get('伤官', 0)
    shi = ten_stats.get('食神', 0)
    guan = ten_stats.get('正官', 0)
    sha = ten_stats.get('七杀', 0)
    yin = ten_stats.get('正印', 0) + ten_stats.get('偏印', 0)
    cai = ten_stats.get('正财', 0) + ten_stats.get('偏财', 0)
    bi = ten_stats.get('比肩', 0) + ten_stats.get('劫财', 0)
    if shang >= 2 and guan >= 1:
        patterns.append(('伤官见官', '才华与规则相战：一生易与上级/体制有口舌分歧，宜走专业自由路线，忌硬碰权威'))
    if (shang >= 2 or shi >= 2) and cai >= 1:
        patterns.append(('食伤生财', '才华变现之象：靠手艺、内容、技术、表达赚钱，钱随艺走，作品即资产'))
    if sha >= 1 and yin >= 1:
        patterns.append(('杀印相生', '压力转化为权柄：越有挑战的岗位越能成就，宜管理、军警法、外科、工程攻坚'))
    if guan >= 1 and yin >= 1:
        patterns.append(('官印相生', '名正言顺之象：利公职、考试、评级晋升，走体制与资历路线稳步向上'))
    if cai >= 1 and guan >= 1:
        patterns.append(('财官双美', '财能生官：经商与管理两相宜，中年后财位俱佳'))
    if (shang >= 2 or shi >= 2) and yin >= 1:
        patterns.append(('伤官配印', '名师之象：才华被学识收束，宜教育、著述、咨询、带徒，晚景清贵'))
    if bi >= 3 and cai <= 0:
        patterns.append(('比劫成群而无财', '群劫争财之忌：合伙分利、朋友借贷最易破财，财务必须独立掌管'))
    if cai >= 2 and ys['yongshen'] != '火' and GAN_WX[day_gan] != '土' and bi == 0:
        patterns.append(('财多身弱', '富屋贫人之象：机会多而担财之力弱，宜借力平台、先强身（学习资历）再求财'))
    if not patterns:
        patterns.append(('五行流通', '命局平稳无特殊格局冲战，人生以稳中有进为主，行喜用运时把握即可跃升'))
    return patterns

def build_career(pillars, strength, ys, adv, ten_stats, dayun, gender, day_gan, birth_year):
    """事业财运·深度"""
    secs = []
    yong, xi, ji = ys['yongshen'], ys['xishen'], ys['jishen']
    cai = ten_stats.get('正财', 0) + ten_stats.get('偏财', 0)
    guan = ten_stats.get('正官', 0) + ten_stats.get('七杀', 0)
    shishang = ten_stats.get('食神', 0) + ten_stats.get('伤官', 0)
    yin = ten_stats.get('正印', 0) + ten_stats.get('偏印', 0)
    bi = ten_stats.get('比肩', 0) + ten_stats.get('劫财', 0)

    # 总纲
    gangyao = []
    if guan and cai:
        gangyao.append('财官双现，事业有台阶：适合在体系内晋升或管理岗求发展，名利可双收')
    elif cai and not guan:
        gangyao.append('财星显而官星弱：对钱敏感、务实求财，宜经商/销售/金融/生意路线，不宜久居科层')
    elif guan and not cai:
        gangyao.append('官星显而财星弱：责任心与纪律性强，宜公职/管理/法律/技术权威路线，靠位置与资历立身')
    elif shishang:
        gangyao.append('食伤吐秀，财官不显：才华即本钱，宜专业技术、创作输出、教育咨询，以"食伤生财"变现')
    else:
        gangyao.append('财官食伤俱不显：宜深耕一技之长，以印星（资历/证书/平台）立身，稳中求进')
    if bi >= 2:
        gangyao.append(f'比劫{bi}见：同行竞争与合伙分利是终生课题，财务独立、股权清晰是第一纪律')
    if yin >= 2:
        gangyao.append(f'印星{yin}见：学历、证书、平台背书是你的护身符，逢险有贵人资历兜底')
    secs.append(_quote('【总纲】' + '；'.join(gangyao) + '。'))

    # 五行行业梯队
    secs.append(_txt(f'用神为{yong}、喜神为{xi}——行业选择以补{yong}为纲：'))
    WX_IND = {
        '木': ['教育培训', '文化出版', '纺织服装', '园林木材', '医药保健', '文创设计'],
        '火': ['互联网/人工智能', '能源电力', '餐饮美食', '广告传媒', '演艺娱乐', '美容光电'],
        '土': ['房地产建筑', '农业土产', '仓储物流', '陶瓷石材', '中介服务', '行政管理'],
        '金': ['金融证券', '机械制造', '五金珠宝', '汽车交通', '法律司法', '医疗器械'],
        '水': ['贸易流通', '旅游运输', '水产饮品', '传媒网络', '咨询服务', '冷链物流'],
    }
    rows = []
    rows.append(['首选', '、'.join(WX_IND[yong]), f'补用神{yong}，事半功倍'])
    rows.append(['次选', '、'.join(WX_IND[xi]) if xi != yong else '同首选', f'喜神{xi}辅助，平稳有利'])
    avoid_wx = ji[0]
    rows.append(['谨慎', '、'.join(WX_IND[avoid_wx]), f'忌神{avoid_wx}行业，付出多回报少'])
    secs.append(_table(['梯队', '方向', '命理逻辑'], rows))

    # 近五年流年详解
    cur_gz = _current_dayun_gz(dayun)
    now = datetime.now().year
    secs.append(_txt(f'近五年流年详解（当前大运{cur_gz or "-"}）：'))
    rows = []
    for y in range(now - 1, now + 4):
        ln = liunian_detail(y, pillars, ys, cur_gz, day_gan)
        sig = _career_signal(ln, ys, ten_stats, day_gan)
        rows.append([str(ln['year']), ln['gz'], f"{ln['gan']}{ln['shishen_gan']}/{ln['zhi']}{ln['shishen_zhi']}",
                     ln['star'], sig])
    secs.append(_table(['年份', '干支', '十神', '评级', '事业信号与建议'], rows))

    # 月度节奏（今年）
    secs.append(_txt(f'{now}年月度节奏（签约/启动/收束择时）：'))
    rows = _month_rows(now, pillars, ys, day_gan)
    secs.append(_table(['流月', '公历时段', '吉凶', '动作建议'], rows))

    # 决策建议
    tips = []
    tips.append(f'重大签约、开张、提价宜压在{yong}、{xi}旺相之月；忌神{ji[0]}旺月只守不攻')
    if bi >= 1:
        tips.append('合伙必立字据、股权分明；宁可少赚，不可糊涂账')
    if cai == 0:
        tips.append('原局无财星：现金流必须短链条——收现、预付、服务制，忌长账期重垫资')
    tips.append(f'发展方位宜{adv["lucky_dir"][0]}，办公座位朝{adv["lucky_dir"][0]}，多用{adv["lucky_colors"][0]}系')
    secs.append(_list(tips))
    return {'title': '事业财运', 'icon': '💼', 'sections': secs}

def _career_signal(ln, ys, ten_stats, day_gan):
    g, z = ln['gan'], ln['zhi']
    sg, sz = ln['shishen_gan'], ln['shishen_zhi']
    parts = []
    if '财' in sg or '财' in sz:
        parts.append('财星当值——求财机会现，主动接单谈价')
    if '官' in sg or '杀' in sg or '官' in sz or '杀' in sz:
        parts.append('官杀当值——职位责任加身，利晋升考试，防压力官非')
    if '印' in sg or '印' in sz:
        parts.append('印星当值——利学习考证、贵人提携、文书合同')
    if '食' in sg or '伤' in sg or '食' in sz or '伤' in sz:
        parts.append('食伤当值——才华输出旺，利创作表达，防口舌顶撞')
    if '比' in sg or '劫' in sg or '比' in sz or '劫' in sz:
        parts.append('比劫当值——竞争分利之象，防合伙破财、朋友借贷')
    key_notes = [n for n in ln['notes'] if n.startswith(('⚠⚠', '★', '✅'))]
    if key_notes:
        parts.append('；'.join(n.lstrip('★⚠✅ ') for n in key_notes[:2]))
    if not parts:
        parts.append(f"{ln['label']}年——按部就班，随大运节奏")
    return '。'.join(parts[:3]) + '。'

def _current_dayun_gz(dayun):
    now = datetime.now().year
    for d in (dayun or {}).get('dayun_list', []):
        yrs = str(d.get('year_range', '')).split('-')
        if len(yrs) == 2:
            try:
                if int(yrs[0]) <= now <= int(yrs[1]):
                    return d.get('gan_zhi')
            except Exception:
                pass
    return None

def _next_dayun(dayun):
    now = datetime.now().year
    for d in (dayun or {}).get('dayun_list', []):
        yrs = str(d.get('year_range', '')).split('-')
        if len(yrs) == 2:
            try:
                if int(yrs[0]) > now:
                    return d
            except Exception:
                pass
    return None

def _month_rows(year, pillars, ys, day_gan):
    rows = []
    yg = year_ganzhi(year)[0]
    order = '寅卯辰巳午未申酉戌亥子丑'
    for mz in order:
        mg = month_gan(yg, mz)
        gz = mg + mz
        score = rate_element(GAN_WX[mg], ys) * 0.4 + rate_element(ALL_WX[mz], ys) * 0.7
        rels = zhi_relations(mz, pillars)
        s1, n1 = rate_relations(rels, ys)
        score += s1 * 0.6
        star, label = star_of(score)
        act = _month_action(label, mz, ys)
        rows.append([f'{mz}月({gz})', MONTH_SOLAR[mz], star, act])
    return rows

def _month_action(label, mz, ys):
    if label == '大吉':
        return '全年最佳窗口：签约、启动、谈判、开张皆宜'
    if label == '吉':
        return '顺势推进：谈合作、提交成果、拓展人脉'
    if label == '平':
        return '按部就班：处理日常事务，蓄力观望'
    if label == '小凶':
        return '谨慎行事：勿做重大决策，防口舌文书'
    return '大事绕开：不启动新摊子，守成为上，注意健康'

def build_luck(pillars, strength, ys, ten_stats, dayun, day_gan, birth_year, gender):
    """大运流年·深度"""
    secs = []
    now = datetime.now().year
    dl = (dayun or {}).get('dayun_list', [])
    cur = None
    for d in dl:
        yrs = str(d.get('year_range', '')).split('-')
        if len(yrs) == 2:
            try:
                if int(yrs[0]) <= now <= int(yrs[1]):
                    cur = d
                    break
            except Exception:
                pass
    if cur:
        gz = cur['gan_zhi']
        g, z = gz[0], gz[1]
        secs.append(_quote(f'【当前大运】{gz}（{cur.get("age_range_xu","")}，{cur.get("year_range","")}）'
                           f'——{g}为{shishen_of(day_gan, g)}、{z}藏{"/".join(CANG_GAN[z])}。'))
        # A面B面
        score_g = rate_element(GAN_WX[g], ys)
        score_z = rate_element(ALL_WX[z], ys)
        rels = zhi_relations(z, pillars)
        rs, notes = rate_relations(rels, ys)
        total = score_g + score_z + rs
        a_txt, b_txt = _dayun_ab(gz, total, notes, ys, day_gan)
        secs.append(_txt(f'A面（顺用）：{a_txt}'))
        secs.append(_txt(f'B面（逆用）：{b_txt}'))
        if notes:
            secs.append(_list(notes))
    # 十年峰谷表
    if cur:
        yrs = str(cur.get('year_range', '')).split('-')
        y0 = int(yrs[0]) if len(yrs) == 2 else now
        secs.append(_txt('本运十年峰谷：'))
        rows = []
        for y in range(max(y0, now - 1), y0 + 10):
            ln = liunian_detail(y, pillars, ys, cur['gan_zhi'], day_gan)
            note = ln['notes'][0].lstrip('★⚠✅○△ ') if ln['notes'] else f"{ln['shishen_gan']}{ln['shishen_zhi']}年，{ln['label']}"
            rows.append([str(ln['year']), ln['gz'], ln['star'], note[:40]])
        secs.append(_table(['年份', '流年', '评级', '要点'], rows))
    nxt = _next_dayun(dayun)
    if nxt:
        gz = nxt['gan_zhi']
        g, z = gz[0], gz[1]
        secs.append(_txt(f'【下一个大运】{gz}（{nxt.get("age_range_xu","")}，{nxt.get("year_range","")}）——'
                         f'{g}{GAN_WX[g]}{shishen_of(day_gan, g)}透干、{z}{ALL_WX[z]}值地。'
                         + _next_dayun_outlook(gz, ys, pillars, day_gan)))
    return {'title': '大运流年', 'icon': '🎋', 'sections': secs}

def _dayun_ab(gz, total, notes, ys, day_gan):
    yong = ys['yongshen']
    if total >= 2:
        a = f'{gz}运五行扶起用神{yong}，十年主升：事业财富皆可主动扩张，敢想敢为'
        b = '顺境中防骄满破财，旺运亦须留三分余地，忌孤注一掷'
    elif total <= -2:
        a = '逆境修内功：学习、考证、攒资历作品，把低谷变成蓄力期'
        b = f'{gz}运克制用神{yong}，十年主守：不宜大投资大变动，健康与现金流优先'
    else:
        a = f'{gz}运平中带机：跟随流年喜用年份（{yong}、{ys["xishen"]}五行之年）重点发力'
        b = '平运忌折腾：频繁换赛道反而耗散，守住主线积累复利'
    return a, b

def _next_dayun_outlook(gz, ys, pillars, day_gan):
    g, z = gz[0], gz[1]
    sg = shishen_of(day_gan, g)
    wxg, wxz = GAN_WX[g], ALL_WX[z]
    parts = []
    if wxg == ys['yongshen'] or wxz == ys['yongshen']:
        parts.append('新运扶用神，晚段行情转旺，宜提前布局')
    elif wxg in ys['jishen'] or wxz in ys['jishen']:
        parts.append('新运带忌神，届时宜守成转型，减少扩张')
    else:
        parts.append('新运五行平和，顺势过渡')
    rels = zhi_relations(z, pillars)
    chong = [r for r in rels if r['type'] == '冲']
    if chong:
        parts.append(f'运支{z}冲{chong[0]["desc"][1:]}，换运前后人事环境必有变动，提前一年收束布局')
    parts.append(f'{g}为{sg}——该十年人生主题围绕"{sg}"展开')
    return '；'.join(parts) + '。'

def build_marriage(pillars, strength, ys, ten_stats, dayun, gender, day_gan, birth_year):
    secs = []
    day_zhi = pillars['day'][1]
    spouse_ss = ('正财', '偏财') if gender == '男' else ('正官', '七杀')
    spouse_wx = WX_KE[GAN_WX[day_gan]] if gender == '男' else WX_KE_WO[GAN_WX[day_gan]]
    n_zheng = ten_stats.get(spouse_ss[0], 0)
    n_pian = ten_stats.get(spouse_ss[1], 0)
    total = n_zheng + n_pian
    star_name = '妻星(财)' if gender == '男' else '夫星(官杀)'

    # 定位配偶星
    pos_found = []
    for pos in ['year', 'month', 'hour']:
        g = pillars[pos][0]
        if shishen_of(day_gan, g) in spouse_ss:
            pos_found.append(f'{POS_NAME[pos]}天干透{g}')
    for pos in ['year', 'month', 'day', 'hour']:
        z = pillars[pos][1]
        if shishen_of(day_gan, CANG_GAN[z][0]) in spouse_ss:
            pos_found.append(f'{POS_NAME[pos]}地支{z}藏{CANG_GAN[z][0]}')

    txt = f'{star_name}五行属{spouse_wx}，命中{"、".join(pos_found) if pos_found else "不透不藏"}。'
    if total == 0:
        txt += '配偶星不现：缘分偏淡或晚婚之象，感情宜主动争取，择偶勿过于理想化；婚缘多应在配偶星出现之大运流年。'
    elif n_zheng == 1 and n_pian == 0:
        txt += '正星一位清纯：感情专一稳定，婚姻质量较高，宜珍惜眼前人。'
    elif total == 1:
        txt += '配偶星一位：感情专注，婚姻稳定度好。'
    else:
        txt += f'配偶星{total}位偏多：感情选择多、异性缘旺，但易生波折纠葛，婚前宜多观察、婚后守界限。'
    secs.append(_txt(txt))

    # 夫妻宫
    gong_txt = f'夫妻宫（日支）坐{day_zhi}{ALL_WX[day_zhi]}，藏{"/".join(CANG_GAN[day_zhi])}。'
    zhi_ss = shishen_of(day_gan, CANG_GAN[day_zhi][0])
    gong_txt += f'宫位本气为{zhi_ss}：'
    gong_map = {
        '正财': '配偶贤惠务实、善理家财，因妻得福',
        '偏财': '配偶能干大方、社交活跃，感情热烈但需经营',
        '正官': '配偶端正自律、有责任心，家中规矩分明',
        '七杀': '配偶个性强势能干，相处如战友，宜多包容',
        '正印': '配偶温和顾家、像长辈般照顾你，婚姻安稳',
        '偏印': '配偶内敛有主见、兴趣独特，精神交流重于形式',
        '食神': '配偶乐天知趣、生活有情调，婚姻轻松',
        '伤官': '配偶才华外露心气高，相处多磨合，忌彼此挑剔',
        '比肩': '夫妻如朋友伙伴，平等独立，但浪漫偏淡',
        '劫财': '配偶个性强、开销主张大，财务宜共同规划防争执',
    }
    gong_txt += gong_map.get(zhi_ss, '平稳。')
    if ALL_WX[day_zhi] == ys['yongshen'] or ALL_WX[day_zhi] == ys['xishen']:
        gong_txt += f'且{day_zhi}{ALL_WX[day_zhi]}为命中喜用——配偶是你的贵人，婚后运势上扬。'
    elif ALL_WX[day_zhi] in ys['jishen']:
        gong_txt += f'惟{day_zhi}{ALL_WX[day_zhi]}为命中忌神——婚后易因配偶消耗，宜保持经济与精神的适度独立。'
    secs.append(_txt(gong_txt))

    # 冲合婚姻宫
    warn = []
    for pos in ['year', 'month', 'hour']:
        z = pillars[pos][1]
        if _pair_in(z, day_zhi, LIU_CHONG):
            warn.append(f'{POS_NAME[pos]}{z}冲夫妻宫{day_zhi}：家庭关系易有聚少离多或口角之争，逢{z}年{day_zhi}年尤需经营')
        if _pair_in(z, day_zhi, XING):
            warn.append(f'{POS_NAME[pos]}{z}刑夫妻宫{day_zhi}：婚姻中易有隐性摩擦，沟通宜坦诚')
    for (a, b), wx in LIU_HE.items():
        for pos in ['year', 'month', 'hour']:
            z = pillars[pos][1]
            if (z == a and day_zhi == b) or (z == b and day_zhi == a):
                warn.append(f'{POS_NAME[pos]}{z}合夫妻宫{day_zhi}化{wx}：家人羁绊深，婚姻受家庭影响大')
    if warn:
        secs.append(_list(warn))

    # 婚期/感情信号年
    now = datetime.now().year
    rows = []
    for y in range(now, now + 5):
        ln = liunian_detail(y, pillars, ys, _current_dayun_gz(dayun), day_gan)
        sig = []
        if '财' in ln['shishen_gan'] and gender == '男':
            sig.append('妻星透干，感情机会年')
        if ('官' in ln['shishen_gan'] or '杀' in ln['shishen_gan']) and gender == '女':
            sig.append('夫星透干，感情机会年')
        for r in zhi_relations(ln['zhi'], pillars):
            if r['pos'] == 'day' and r['type'] == '合':
                sig.append('合动婚姻宫，婚恋大事之象')
            if r['pos'] == 'day' and r['type'] == '冲':
                sig.append('冲动婚姻宫，感情波动防争执')
        rows.append([str(y), ln['gz'], ln['star'], '；'.join(sig) if sig else '感情平顺'])
    secs.append(_table(['年份', '流年', '评级', '感情信号'], rows))
    secs.append(_list([
        f'择偶宜五行喜{spouse_wx}、{ys["yongshen"]}之人；属相以与日支{day_zhi}三合六合者为佳',
        f'婚恋有利方位：{ys and "、".join(adv_dir(ys))}；婚期多应在合动夫妻宫或配偶星旺相之年',
    ]))
    return {'title': '婚姻感情', 'icon': '💞', 'sections': secs}

def adv_dir(ys):
    WX_DIR = {'木': '东方', '火': '南方', '土': '本地中部', '金': '西方', '水': '北方'}
    return [WX_DIR[ys['yongshen']], WX_DIR[ys['xishen']]] if ys['xishen'] != ys['yongshen'] else [WX_DIR[ys['yongshen']]]

WX_ORGAN = {'木': '肝胆、筋腱、颈肩腰椎、神经系统', '火': '心脏、血压、眼睛、睡眠、血液循环',
            '土': '脾胃、消化、肌肉、血糖代谢', '金': '肺、呼吸道、大肠、皮肤、骨骼牙齿',
            '水': '肾、泌尿、内分泌、生殖系统、耳'}

def build_health(pillars, strength, ys, ten_stats, dayun, day_gan):
    secs = []
    wc = ys['wx_count']
    ranked = sorted(wc.items(), key=lambda kv: kv[1])
    weak = [w for w, v in ranked if v < 1.0]
    strong = [w for w, v in ranked if v >= 2.5]
    txt = '五行力量与健康对应：'
    secs.append(_table(['五行', '力量', '对应系统', '状态'],
                       [[w, str(wc[w]), WX_ORGAN[w],
                         '偏弱·重点温养' if wc[w] < 1.0 else ('偏旺·防过亢' if wc[w] >= 2.5 else '平和')]
                        for w in ['木', '火', '土', '金', '水']]))
    detail = []
    for w in weak:
        detail.append(f'{w}气不足（{wc[w]}分）——{WX_ORGAN[w]}为薄弱环节，宜温补养护、定期检查')
    for w in strong:
        detail.append(f'{w}气偏旺（{wc[w]}分）——{WX_ORGAN[w]}功能亢进，防炎症、上火、劳损类问题')
    if ys.get('tiaohou'):
        t = ys['tiaohou']
        detail.append(f'命局寒暖失衡需{t}调候——体质偏{"寒" if t == "火" else "燥热"}，注意保暖{"补水润燥" if t == "水" else "温阳驱寒"}')
    if strength['level'] <= 2:
        detail.append('日主偏弱：元气与免疫力是根本，忌熬夜透支——熬夜即漏根，规律作息胜过任何补品')
    elif strength['level'] >= 4:
        detail.append('日主偏强：精力旺而易过用，防三高、炎症、情绪上火，宜运动泄压')
    secs.append(_list(detail))
    # 冲刑年健康预警
    now = datetime.now().year
    warns = []
    for y in range(now, now + 6):
        ln = liunian_detail(y, pillars, ys, _current_dayun_gz(dayun), day_gan)
        xing_chong = [n for n in ln['notes'] if ('刑' in n or '冲' in n) and ('日' in n or '月' in n or '⚠⚠' in n)]
        if xing_chong:
            warns.append(f'{ln["year"]}（{ln["gz"]}）：{xing_chong[0].lstrip("⚠△★ ")}——该年防外伤、手术、旧疾复发，忌极限运动，宜安排体检')
    if warns:
        secs.append(_txt('健康预警年份：'))
        secs.append(_list(warns))
    secs.append(_list([
        f'养生方向：多亲近{ys["yongshen"]}属性——{ "绿色植被/舒展运动" if ys["yongshen"]=="木" else "阳光 warmth/有氧出汗" if ys["yongshen"]=="火" else "规律饮食/接地稳重" if ys["yongshen"]=="土" else "呼吸训练/洁净空气" if ys["yongshen"]=="金" else "游泳静坐/充足饮水" }',
        f'饮食五味：{ {"木":"酸味入肝宜适度","火":"苦味清心宜适度","土":"甘味养脾宜适度","金":"辛味润肺宜适度","水":"咸味入肾宜适度"}[ys["yongshen"]] }，忌神{ys["jishen"][0]}对应之味宜清淡',
        f'体检安排宜放在喜用神当旺之月（{ys["yongshen"]}、{ys["xishen"]}月令），身体状态最真实',
    ]))
    return {'title': '健康养生', 'icon': '🏥', 'sections': secs}

def build_personality(pillars, strength, ys, ten_stats, day_gan, day_zhi=None):
    secs = []
    dwx = GAN_WX[day_gan]
    yy = GAN_YY[day_gan]
    traits = {
        ('阳', '木'): '甲木参天——正直上进、有担当有恻隐之心，宁折不弯，是天然的主心骨',
        ('阴', '木'): '乙木藤萝——柔韧灵活、善借势生长，外圆内方，适应力与亲和力强',
        ('阳', '火'): '丙火太阳——热情磊落、光明外向，行动力强感染力强，但性急不耐阴柔',
        ('阴', '火'): '丁火灯烛——细腻敏锐、洞察人心，外静内热，专注时能量惊人',
        ('阳', '土'): '戊土高山——厚重诚信、稳如磐石，包容务实，但固执起来九牛拉不回',
        ('阴', '土'): '己土田园——温和细致、任劳任怨，善于滋养他人，但易多思多虑放不下',
        ('阳', '金'): '庚金斧钺——果决刚毅、义气分明，执行力与突破力强，但易强硬伤人',
        ('阴', '金'): '辛金珠玉——精致敏感、自尊心强，审美与细节把控佳，但易纠结面子',
        ('阳', '水'): '壬水江河——聪慧大气、奔流不息，谋略与变通力顶尖，但易任性难拘束',
        ('阴', '水'): '癸水雨露——内敛多思、直觉惊人，渗透力与同理心强，但易阴郁内耗',
    }
    secs.append(_quote(f'【日主本性】{day_gan}为{yy}{dwx}：{traits[(yy, dwx)]}'))
    lv = strength['level']
    if lv >= 4:
        secs.append(_txt(f'旺衰{strength["level_name"]}：自我意识强、有主见能扛事，天生不服输；须修谦和、听得进人言，刚而易折是最大课题。'))
    elif lv <= 2:
        secs.append(_txt(f'旺衰{strength["level_name"]}：为人随和敏感、善察言观色，韧性在"扛"不在"争"；须增自信、立边界，过度体谅他人易内耗。'))
    else:
        secs.append(_txt(f'旺衰{strength["level_name"]}：性格中和、进退有度，环境适应力强，是能屈能伸的实用主义者。'))
    # 十神性格
    ten_txt = []
    shang = ten_stats.get('伤官', 0); shi = ten_stats.get('食神', 0)
    yin = ten_stats.get('正印', 0) + ten_stats.get('偏印', 0)
    cai = ten_stats.get('正财', 0) + ten_stats.get('偏财', 0)
    guan = ten_stats.get('正官', 0); sha = ten_stats.get('七杀', 0)
    bi = ten_stats.get('比肩', 0); jie = ten_stats.get('劫财', 0)
    if shang + shi >= 2: ten_txt.append(f'食伤{shang+shi}见——才华外露，口才/审美/手艺/文字至少占一样，心气高不服管，脑子停不下来')
    if yin >= 2: ten_txt.append(f'印星{yin}见——学习力与直觉强，重精神世界，喜静好思，逢险有贵人兜底')
    if cai >= 2: ten_txt.append(f'财星{cai}见——务实目标导向，对资源机会敏感，行动落地能力强')
    if guan + sha >= 2: ten_txt.append(f'官杀{guan+sha}见——自律有责任感，抗压能力强，但也易自我施压、活得紧绷')
    if bi + jie >= 2: ten_txt.append(f'比劫{bi+jie}见——独立好胜，朋友义气重，财物观豁达但易被分利')
    if ten_txt:
        secs.append(_list(ten_txt))
    # 格局主线
    pats = pattern_check(pillars, ten_stats, ys, day_gan)
    secs.append(_txt('格局主线：'))
    secs.append(_list([f'【{p[0]}】{p[1]}' for p in pats]))
    dz = pillars['day'][1]
    secs.append(_txt(f'日支{dz}坐{ALL_WX[dz]}（{shishen_of(day_gan, CANG_GAN[dz][0])}）为内心世界：对外表现与私下真实状态或有反差，亲密关系中呈现"{CANG_GAN[dz][0]}{GAN_WX[CANG_GAN[dz][0]]}"之性。'))
    return {'title': '性格特征', 'icon': '🧭', 'sections': secs}

def build_study(pillars, strength, ys, ten_stats, dayun, day_gan):
    secs = []
    yin = ten_stats.get('正印', 0) + ten_stats.get('偏印', 0)
    shi = ten_stats.get('食神', 0) + ten_stats.get('伤官', 0)
    guan = ten_stats.get('正官', 0) + ten_stats.get('七杀', 0)
    if yin >= 2:
        secs.append(_txt(f'印星{yin}见——记忆力理解力俱佳，坐得住冷板凳，学历路线、考证深造如鱼得水；正印主系统性学习，偏印主偏才悟性（玄艺医卜理工直觉）。'))
    elif yin == 1:
        secs.append(_txt('印星一位——学习能力中上，有文凭缘，深造可行但需主动用功。'))
    else:
        secs.append(_txt('印星不显——死读书非所长，实践出真知：职业教育、学徒制、干中学路线更能出成绩。'))
    if shi >= 2:
        secs.append(_txt(f'食伤{shi}见——思维活跃表达欲强，创意、写作、演讲、技术发明有天赋；但考场易粗心，须以印星（规律复习）收束才华。'))
    if guan >= 1:
        secs.append(_txt(f'官星{guan}见——自律守规则，适应考试制度，公职类考试（公务员/编内/司法）有利。'))
    now = datetime.now().year
    rows = []
    for y in range(now, now + 4):
        ln = liunian_detail(y, pillars, ys, _current_dayun_gz(dayun), day_gan)
        sig = []
        if '印' in ln['shishen_gan'] or '印' in ln['shishen_zhi']:
            sig.append('印星年——利考试、录取、证书、文书')
        if '官' in ln['shishen_gan'] or '杀' in ln['shishen_gan']:
            sig.append('官杀年——利功名、公职、纪律性考核')
        if '食' in ln['shishen_gan'] or '伤' in ln['shishen_gan']:
            sig.append('食伤年——利才艺展示、创作类考核，防粗心')
        rows.append([str(y), ln['gz'], ln['star'], '；'.join(sig) if sig else f'{ln["label"]}年，稳扎稳打'])
    secs.append(_table(['年份', '流年', '考运', '提示'], rows))
    secs.append(_list([
        f'书桌/考场方位宜{adv_dir(ys)[0]}，文具书包多用{ys["yongshen"]}属性色（{"绿青" if ys["yongshen"]=="木" else "红紫" if ys["yongshen"]=="火" else "黄棕" if ys["yongshen"]=="土" else "白金" if ys["yongshen"]=="金" else "黑蓝"}系）',
        f'备考冲刺宜安排在{ys["yongshen"]}、{ys["xishen"]}旺相之月，头脑最清明',
        '重大考试前一晚忌熬夜（耗印星），忌与人争执（动官杀）',
    ]))
    return {'title': '学业考试', 'icon': '📚', 'sections': secs}

def build_social(pillars, strength, ys, ten_stats, dayun, day_gan):
    secs = []
    bi = ten_stats.get('比肩', 0); jie = ten_stats.get('劫财', 0)
    yin = ten_stats.get('正印', 0) + ten_stats.get('偏印', 0)
    guan = ten_stats.get('正官', 0) + ten_stats.get('七杀', 0)
    if bi + jie >= 2:
        secs.append(_txt(f'比劫{bi+jie}见——同辈朋友缘极厚，社交圈广、号召力强；但"群劫争财"，合伙、借贷、金钱往来是人际最大雷区，救急不救穷、亲兄弟明算账。'))
    elif bi + jie == 0:
        secs.append(_txt('比劫不现——独来独往型，朋友圈精而不多，深度合作宜找属相三合、五行互补之人。'))
    else:
        secs.append(_txt('比劫一位——人际平和，有同辈助力，合作运尚可，账目清晰即可长久。'))
    if yin >= 1:
        secs.append(_txt(f'印星{yin}见——贵人多在长辈、上司、师长之中；关键时刻总有人以资历、资源、背书相护，宜主动维护长辈缘。'))
    if guan >= 1:
        secs.append(_txt(f'官星{guan}见——与体制、权威、规则的关系是人际主线：敬而不畏、亲而有间，官面人脉是你的隐形资产。'))
    day_zhi = pillars['day'][1]
    SAN_HE_MAP = {'子': '申辰', '丑': '巳酉', '寅': '午戌', '卯': '亥未', '辰': '申子', '巳': '酉丑',
                  '午': '寅戌', '未': '亥卯', '申': '子辰', '酉': '巳丑', '戌': '寅午', '亥': '卯未'}
    LIU_HE_MAP = {'子': '丑', '丑': '子', '寅': '亥', '亥': '寅', '卯': '戌', '戌': '卯',
                  '辰': '酉', '酉': '辰', '巳': '申', '申': '巳', '午': '未', '未': '午'}
    secs.append(_list([
        f'贵人属相：与日支{day_zhi}三合之{SAN_HE_MAP[day_zhi]}、六合之{LIU_HE_MAP[day_zhi]}为上选；合作共事优先考虑',
        f'贵人方位：{adv_dir(ys)[0]}——出差、搬迁、拓展业务优先此方位',
        f'贵人五行：日主{GAN_WX[day_gan]}弱时需{ys["yongshen"]}，多亲近生于{ {"木":"春","火":"夏","土":"四季末月","金":"秋","水":"冬"}[ys["yongshen"]] }之人',
        '人际雷区：忌神' + ys['jishen'][0] + '旺相之人（性格气场与你相耗），共事保持距离、只谈事不交心',
    ]))
    return {'title': '人际贵人', 'icon': '🤝', 'sections': secs}

def build_summary(pillars, strength, ys, adv, ten_stats, dayun, gender, day_gan, birth_year):
    """总结章：补运+心法+校准点（始终输出）"""
    secs = []
    now = datetime.now().year
    age = now - birth_year
    yong, xi, ji = ys['yongshen'], ys['xishen'], ys['jishen']
    wx_short = ' '.join(f'{w}{int(round(v))}' for w, v in ys['wx_count'].items())
    secs.append(_quote(f'【命局一句话】日主{day_gan}{GAN_WX[day_gan]}，{strength["level_name"]}；五行力量 {wx_short}；'
                       f'用神{yong}、喜神{xi}、忌神{"、".join(ji)}。'
                       f'{"；".join(p[0] for p in pattern_check(pillars, ten_stats, ys, day_gan))}。'))
    # 补运
    bu = []
    bu.append(f'颜色：多用{"、".join(adv["lucky_colors"])}系（衣着/家居/车色）；少用{"、".join(adv["avoid_colors"])}')
    bu.append(f'方位：{adv["lucky_dir"][0]}利发展，{adv["avoid_dir"][0]}忌扩张；居住工作首选{adv["lucky_dir"][0]}')
    bu.append(f'数字：{("、".join(map(str, adv["lucky_nums"])))}为吉（楼层/号码/日期择用）')
    bu.append(f'饰品：{adv["accessory"]}；材质以{yong}属性为要')
    season_map = {'木': '春季舒展筋骨', '火': '夏季适度出汗', '土': '四季末月规律饮食', '金': '秋季呼吸登高', '水': '冬季游泳静养'}
    bu.append(f'运动养生：{season_map[yong]}为佳；忌神{ji[0]}当旺之月（{ {"木":"春","火":"夏","土":"三六九十二月","金":"秋","水":"冬"}[ji[0]] }）减少过劳')
    secs.append(_txt('【补运方案】'))
    secs.append(_list(bu))
    # 心法
    lv = strength['level']
    pats = pattern_check(pillars, ten_stats, ys, day_gan)
    xinfa = []
    if lv >= 4:
        xinfa.append('你最大的敌人不是运势，是"什么都想扛"——学会把"我会做"换成"我不做"，人生反而开阔')
    elif lv <= 2:
        xinfa.append('你的功课是把敏感变成雷达而非内耗——借力（平台/贵人/证书）不是软弱，是身弱者的顶级智慧')
    else:
        xinfa.append('中和之命贵在"择时"——顺境年份敢出手，逆境年份能沉淀，节奏感就是你的天赋')
    if any('伤官' in p[0] for p in pats):
        xinfa.append('才华是你的本钱，口舌是你的漏财口——作品说话，少与权威正面争锋')
    if any('比劫' in p[0] for p in pats):
        xinfa.append('朋友遍天下，账目要分明——人情可以厚，钱据必须清')
    if ys.get('tiaohou'):
        xinfa.append(f'命局需{ys["tiaohou"]}调候——心境上也要"{"取暖：多晒阳光多近热闹" if ys["tiaohou"]=="火" else "降温：多静多水少急躁"}"，环境补运先补心')
    secs.append(_txt('【心法】'))
    secs.append(_list(xinfa))
    # 待验证校准点（回溯历史大运流年）
    cal = []
    cur_gz = _current_dayun_gz(dayun)
    for y in [now - 10, now - 9, now - 5, now - 2, now - 1]:
        if y < birth_year + 3:
            continue
        ln = liunian_detail(y, pillars, ys, cur_gz, day_gan)
        if ln['label'] in ('大吉', '吉'):
            cal.append(f'[ ] {ln["year"]}（{ln["gz"]}，{ln["star"]}）：是否记得该年有贵人/进财/喜事？（{ln["shishen_gan"]}透干之年）')
        elif ln['label'] in ('凶', '小凶'):
            cal.append(f'[ ] {ln["year"]}（{ln["gz"]}，{ln["star"]}）：是否记得该年破财/劳累/健康或人事波动？')
    dl = (dayun or {}).get('dayun_list', [])
    for d in dl:
        yrs = str(d.get('year_range', '')).split('-')
        if len(yrs) == 2:
            try:
                y0, y1 = int(yrs[0]), int(yrs[1])
                if y0 <= now - 8 <= y1 and y0 > birth_year:
                    g = d['gan_zhi'][0]
                    cal.append(f'[ ] {d["gan_zhi"]}大运（{d.get("year_range")}）：{shishen_of(day_gan, g)}当值十年——是否主"{"忙碌求财" if "财" in shishen_of(day_gan, g) else "学习积累" if "印" in shishen_of(day_gan, g) else "事业责任" if ("官" in shishen_of(day_gan, g) or "杀" in shishen_of(day_gan, g)) else "才华输出" if ("食" in shishen_of(day_gan, g) or "伤" in shishen_of(day_gan, g)) else "竞争自立"}"主题？')
                    break
            except Exception:
                pass
    if cal:
        secs.append(_txt('【待验证校准点】（反馈后可修正判断精度）'))
        secs.append(_list(cal[:5]))
    return {'title': '总结与补运', 'icon': '📜', 'sections': secs}

BUILDERS = {
    'career': build_career,
    'luck': build_luck,
    'marriage': build_marriage,
    'health': build_health,
    'personality': build_personality,
    'study': build_study,
    'social': build_social,
}

def build_focus_blocks(focus_ids, ctx):
    """按关键词构建深度分析块；ctx 为统一上下文字典"""
    import inspect
    blocks = []
    for fid in focus_ids:
        fn = BUILDERS.get(fid)
        if not fn:
            continue
        try:
            names = set(inspect.signature(fn).parameters.keys())
            kwargs = {k: v for k, v in ctx.items() if k in names}
            blocks.append(fn(**kwargs))
        except Exception as e:
            import traceback
            traceback.print_exc()
            blocks.append({'title': fid, 'icon': '📄', 'sections': [_txt(f'（该项分析生成失败：{e}）')]})
    return blocks
