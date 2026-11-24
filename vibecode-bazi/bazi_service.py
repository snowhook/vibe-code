#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""八字分析服务模块 — 供 server.py /api/bazi/* 路由调用
排盘复用 bazi-expert skill 的 bazi_core；旺衰/喜用神/喜忌按知识库规则实现。
"""
import os
import sys
import json
import sqlite3
import threading
from datetime import datetime, timedelta

SKILL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'skills', 'bazi-expert')
sys.path.insert(0, os.path.join(SKILL_DIR, 'scripts'))

import pytz  # noqa: E402
import bazi_core  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'bazi.db')
_lock = threading.Lock()

# ── 基础数据 ─────────────────────────────────────────────
GAN = '甲乙丙丁戊己庚辛壬癸'
GAN_WX = {'甲': '木', '乙': '木', '丙': '火', '丁': '火', '戊': '土', '己': '土',
          '庚': '金', '辛': '金', '壬': '水', '癸': '水'}
ZHI_WX = {'子': '水', '丑': '土', '寅': '木', '卯': '木', '辰': '土', '巳': '火',
          '午': '火', '未': '土', '申': '金', '酉': '金', '戌': '土', '亥': '水'}
ALL_WX = dict(GAN_WX)
ALL_WX.update(ZHI_WX)
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

SHISHEN_TABLE = {}
def _init_shishen():
    for dg in GAN:
        dwx, dyy = GAN_WX[dg], GAN_YY[dg]
        for og in GAN:
            owx, oyy = GAN_WX[og], GAN_YY[og]
            same = (dyy == oyy)
            if owx == dwx:
                s = '比肩' if same else '劫财'
            elif WX_SHENG_WO[dwx] == owx:
                s = '偏印' if same else '正印'
            elif WX_SHENG[dwx] == owx:
                s = '食神' if same else '伤官'
            elif WX_KE[dwx] == owx:
                s = '偏财' if same else '正财'
            elif WX_KE_WO[dwx] == owx:
                s = '七杀' if same else '正官'
            else:
                s = '?'
            SHISHEN_TABLE[(dg, og)] = s
_init_shishen()

WX_INFO = {
    '木': {'colors': ['绿色', '青色'], 'dir': '东方', 'nums': [3, 8], 'season': '春季',
           'industry': ['教育培训', '文化传媒', '纺织服装', '园林木材', '医药保健'],
           'organs': '肝胆', 'taste': '酸', 'accessory': '木质手串、绿植'},
    '火': {'colors': ['红色', '紫色', '粉色'], 'dir': '南方', 'nums': [2, 7], 'season': '夏季',
           'industry': ['互联网科技', '能源电力', '餐饮美食', '广告营销', '演艺娱乐'],
           'organs': '心脏小肠', 'taste': '苦', 'accessory': '红玛瑙、朱砂饰品'},
    '土': {'colors': ['黄色', '棕色', '米色'], 'dir': '本地·中部', 'nums': [5, 0], 'season': '四季末月',
           'industry': ['房地产建筑', '农业土产', '仓储物流', '陶瓷石材', '行政管理'],
           'organs': '脾胃', 'taste': '甘', 'accessory': '玉石、陶瓷'},
    '金': {'colors': ['白色', '金色', '银色'], 'dir': '西方', 'nums': [4, 9], 'season': '秋季',
           'industry': ['金融证券', '机械制造', '五金珠宝', '汽车交通', '法律司法'],
           'organs': '肺与大肠', 'taste': '辛', 'accessory': '金银饰品、金属腕表'},
    '水': {'colors': ['黑色', '蓝色', '灰色'], 'dir': '北方', 'nums': [1, 6], 'season': '冬季',
           'industry': ['贸易流通', '旅游运输', '水产饮品', '传媒网络', '咨询服务'],
           'organs': '肾脏膀胱', 'taste': '咸', 'accessory': '黑曜石、水晶'},
}

SEASON_BY_ZHI = {'寅': '春', '卯': '春', '辰': '春', '巳': '夏', '午': '夏', '未': '夏',
                 '申': '秋', '酉': '秋', '戌': '秋', '亥': '冬', '子': '冬', '丑': '冬'}

DEFAULT_FOCUS_KEYWORDS = [
    {'id': 'career', 'name': '事业财运', 'desc': '适合行业·求财方式·事业时机'},
    {'id': 'marriage', 'name': '婚姻感情', 'desc': '配偶特征·感情模式·婚姻建议'},
    {'id': 'health', 'name': '健康养生', 'desc': '体质弱点·养生方向·注意事项'},
    {'id': 'study', 'name': '学业考试', 'desc': '学习能力·适合方向·考运分析'},
    {'id': 'social', 'name': '人际贵人', 'desc': '贵人方位·社交模式·合作关系'},
    {'id': 'personality', 'name': '性格特征', 'desc': '核心性格·优势短板·行为风格'},
    {'id': 'luck', 'name': '大运流年', 'desc': '大运走势·当前运势·关键节点'},
]

PROVINCE_CITIES = {
    '直辖市': ['北京', '上海', '天津', '重庆'],
    '广东': ['广州', '深圳', '佛山', '东莞', '中山', '珠海', '汕头', '江门', '湛江', '茂名', '肇庆', '惠州', '梅州', '汕尾', '河源', '阳江', '清远', '韶关', '揭阳', '云浮', '潮州'],
    '江苏': ['南京', '苏州', '无锡', '常州', '南通', '徐州', '扬州', '镇江', '泰州', '盐城', '淮安', '连云港', '宿迁'],
    '浙江': ['杭州', '宁波', '温州', '嘉兴', '湖州', '绍兴', '金华', '衢州', '舟山', '台州', '丽水'],
    '山东': ['济南', '青岛', '烟台', '潍坊', '临沂', '淄博', '济宁', '泰安', '威海', '德州', '聊城', '滨州', '菏泽', '枣庄', '东营', '日照'],
    '河南': ['郑州', '洛阳', '开封', '新乡', '焦作', '安阳', '平顶山', '许昌', '南阳', '信阳', '周口', '漯河', '驻马店', '商丘', '三门峡', '鹤壁', '濮阳', '济源'],
    '湖北': ['武汉', '宜昌', '襄阳', '荆州', '黄冈', '十堰', '孝感', '荆门', '鄂州', '黄石', '咸宁', '随州', '恩施', '仙桃', '潜江', '天门'],
    '湖南': ['长沙', '株洲', '湘潭', '衡阳', '邵阳', '岳阳', '常德', '张家界', '益阳', '郴州', '永州', '怀化', '娄底', '湘西'],
    '四川': ['成都', '绵阳', '德阳', '南充', '宜宾', '自贡', '乐山', '泸州', '达州', '内江', '遂宁', '广安', '眉山', '资阳', '攀枝花', '巴中', '雅安', '广元', '阿坝', '甘孜', '凉山'],
    '河北': ['石家庄', '唐山', '保定', '邯郸', '邢台', '沧州', '衡水', '廊坊', '承德', '张家口', '秦皇岛'],
    '山西': ['太原', '大同', '阳泉', '长治', '晋城', '朔州', '晋中', '运城', '忻州', '临汾', '吕梁'],
    '辽宁': ['沈阳', '大连', '鞍山', '抚顺', '本溪', '丹东', '锦州', '营口', '阜新', '辽阳', '盘锦', '铁岭', '朝阳', '葫芦岛'],
    '吉林': ['长春', '吉林', '四平', '辽源', '通化', '白山', '松原', '白城', '延边'],
    '黑龙江': ['哈尔滨', '齐齐哈尔', '鸡西', '鹤岗', '双鸭山', '大庆', '伊春', '佳木斯', '七台河', '牡丹江', '黑河', '绥化'],
    '陕西': ['西安', '宝鸡', '咸阳', '渭南', '延安', '汉中', '榆林', '安康', '商洛', '铜川'],
    '甘肃': ['兰州', '嘉峪关', '金昌', '白银', '天水', '武威', '张掖', '平凉', '酒泉', '庆阳', '定西', '陇南', '临夏', '甘南'],
    '青海': ['西宁', '海东', '海北', '黄南', '果洛', '玉树', '海西'],
    '宁夏': ['银川', '石嘴山', '吴忠', '固原', '中卫'],
    '新疆': ['乌鲁木齐', '克拉玛依', '吐鲁番', '哈密', '昌吉', '博尔塔拉', '巴音郭楞', '阿克苏', '喀什', '和田', '伊犁', '塔城', '阿勒泰'],
    '内蒙古': ['呼和浩特', '包头', '乌海', '赤峰', '通辽', '鄂尔多斯', '呼伦贝尔', '巴彦淖尔', '乌兰察布', '兴安盟', '锡林郭勒', '阿拉善'],
    '西藏': ['拉萨', '日喀则', '昌都', '林芝', '山南', '那曲', '阿里'],
    '云南': ['昆明', '曲靖', '玉溪', '保山', '昭通', '丽江', '普洱', '临沧', '楚雄', '红河', '文山', '西双版纳', '大理', '德宏', '怒江', '迪庆'],
    '贵州': ['贵阳', '六盘水', '遵义', '安顺', '毕节', '铜仁', '黔西南', '黔东南', '黔南'],
    '广西': ['南宁', '柳州', '桂林', '梧州', '北海', '防城港', '钦州', '贵港', '玉林', '百色', '贺州', '河池', '来宾', '崇左'],
    '海南': ['海口', '三亚', '儋州', '五指山', '琼海', '文昌', '万宁', '东方', '定安', '屯昌', '澄迈', '临高', '白沙', '昌江', '乐东', '陵水', '保亭', '琼中'],
    '福建': ['福州', '厦门', '莆田', '三明', '泉州', '漳州', '南平', '龙岩', '宁德'],
    '江西': ['南昌', '景德镇', '萍乡', '九江', '新余', '鹰潭', '赣州', '吉安', '宜春', '抚州', '上饶'],
    '安徽': ['合肥', '芜湖', '蚌埠', '淮南', '马鞍山', '淮北', '铜陵', '安庆', '黄山', '滁州', '阜阳', '宿州', '六安', '亳州', '池州', '宣城'],
    '港澳台': ['香港', '澳门', '台北', '高雄', '台中', '台南', '新北', '桃园'],
}

_city_cache = None
def _load_supported_cities():
    global _city_cache
    if _city_cache is None:
        with open(os.path.join(SKILL_DIR, 'references', 'cities.json'), encoding='utf-8') as f:
            _city_cache = json.load(f)
    return _city_cache

def get_cities():
    supported = set(_load_supported_cities().keys())
    result = []
    for prov, cities in PROVINCE_CITIES.items():
        avail = [c for c in cities if c in supported]
        if avail:
            result.append({'province': prov, 'cities': avail})
    return result

# ── 农历 ─────────────────────────────────────────────────
LUNAR_MONTH_CN = {1: '正月', 2: '二月', 3: '三月', 4: '四月', 5: '五月', 6: '六月',
                  7: '七月', 8: '八月', 9: '九月', 10: '十月', 11: '十一月', 12: '腊月'}
DAY_CN = ['初一','初二','初三','初四','初五','初六','初七','初八','初九','初十',
          '十一','十二','十三','十四','十五','十六','十七','十八','十九','二十',
          '廿一','廿二','廿三','廿四','廿五','廿六','廿七','廿八','廿九','三十']

def lunar_day_cn(d):
    return DAY_CN[d - 1] if 1 <= d <= 30 else str(d)

def lunar_to_solar(lunar_year, lunar_month, lunar_day, is_leap=False):
    """农历转公历：借助 skill 的朔望月引擎（东八区自然日口径）"""
    approx = datetime(lunar_year, 2, 1, 12, 0, tzinfo=pytz.UTC)
    months, _ = bazi_core.build_lunar_months_around(approx, 'Asia/Shanghai')
    zheng_idx = None
    for i, m in enumerate(months):
        if m.month_no == 1 and not m.is_leap:
            loc = bazi_core.from_utc(m.start_utc, 'Asia/Shanghai')
            if loc.year == lunar_year and loc.month <= 4:
                zheng_idx = i
                break
    if zheng_idx is None:
        raise ValueError('无法定位农历正月（支持范围约1900-2100年）')
    for j in range(zheng_idx, min(zheng_idx + 16, len(months))):
        m = months[j]
        if m.month_no == lunar_month and m.is_leap == is_leap:
            loc = bazi_core.from_utc(m.start_utc, 'Asia/Shanghai')
            target = loc + timedelta(days=lunar_day - 1)
            # 校验仍在该朔望月内
            if target.replace(tzinfo=pytz.UTC) < m.end_utc.replace(tzinfo=None) if m.end_utc.tzinfo is None else True:
                pass
            return target.date()
    raise ValueError('农历日期无效（该月无此日或无该闰月）')

def lunar_month_days(lunar_year, lunar_month, is_leap=False):
    try:
        approx = datetime(lunar_year, 2, 1, 12, 0, tzinfo=pytz.UTC)
        months, _ = bazi_core.build_lunar_months_around(approx, 'Asia/Shanghai')
        for i, m in enumerate(months):
            if m.month_no == 1 and not m.is_leap:
                loc = bazi_core.from_utc(m.start_utc, 'Asia/Shanghai')
                if loc.year == lunar_year and loc.month <= 4:
                    for j in range(i, len(months)):
                        mm = months[j]
                        if mm.month_no == lunar_month and mm.is_leap == is_leap:
                            return (mm.end_utc - mm.start_utc).days
                    break
    except Exception:
        pass
    return 30

def has_leap_month(lunar_year, lunar_month):
    try:
        approx = datetime(lunar_year, 2, 1, 12, 0, tzinfo=pytz.UTC)
        months, _ = bazi_core.build_lunar_months_around(approx, 'Asia/Shanghai')
        for m in months:
            if m.month_no == lunar_month and m.is_leap:
                loc = bazi_core.from_utc(m.start_utc, 'Asia/Shanghai')
                if abs(loc.year - lunar_year) <= 1:
                    return True
    except Exception:
        pass
    return False

# ── 排盘 ─────────────────────────────────────────────────
def compute_chart(year, month, day, hour, minute, gender, city):
    cities = _load_supported_cities()
    if city not in cities:
        raise ValueError(f'城市不支持: {city}')
    info = cities[city]
    tzobj = pytz.timezone(info['tz'])
    dt_local = tzobj.localize(datetime(year, month, day, hour, minute))
    return bazi_core.locate_lunar_date(dt_local, info['lat'], info['lon'], info['tz'], True, gender)

# ── 旺衰判定 ─────────────────────────────────────────────
def _root_strength(day_gan, zhi):
    dwx = GAN_WX[day_gan]
    best = 0
    for idx, cg in enumerate(CANG_GAN[zhi]):
        cwx = GAN_WX[cg]
        if cwx == dwx:
            best = max(best, 3 if idx == 0 else 2)
        elif WX_SHENG_WO[dwx] == cwx:
            best = max(best, 2 if idx == 0 else 1)
    return best

def judge_strength(pillars):
    day_gan, _ = pillars['day'][0], pillars['day'][1]
    dwx = GAN_WX[day_gan]
    month_zhi = pillars['month'][1]
    details = []

    m_wx = GAN_WX[CANG_GAN[month_zhi][0]]
    if m_wx == dwx:
        ling_score, ling_desc = 5, f'日主{day_gan}{dwx}生于{month_zhi}月，月令{m_wx}与日主同类，当令'
    elif WX_SHENG_WO[dwx] == m_wx:
        ling_score, ling_desc = 4, f'日主{day_gan}{dwx}生于{month_zhi}月，月令{m_wx}生日主，得生'
    elif WX_SHENG[dwx] == m_wx:
        ling_score, ling_desc = -3, f'日主{day_gan}{dwx}生于{month_zhi}月，月令{m_wx}泄日主之气，失令'
    elif WX_KE[dwx] == m_wx:
        ling_score, ling_desc = -4, f'日主{day_gan}{dwx}生于{month_zhi}月，日主克月令{m_wx}，耗力失令'
    else:
        ling_score, ling_desc = -5, f'日主{day_gan}{dwx}生于{month_zhi}月，月令{m_wx}克日主，失令受克'
    details.append(('得令', ling_score, ling_desc))

    di_score = 0
    di_parts = []
    names = {0: '无根', 1: '微根(余气)', 2: '中根', 3: '强根(禄旺)'}
    for pos, key in [('年支', 'year'), ('日支', 'day'), ('时支', 'hour')]:
        zhi = pillars[key][1]
        rs = _root_strength(day_gan, zhi)
        pts = {0: 0, 1: 1, 2: 2, 3: 3}[rs]
        di_score += pts
        di_parts.append(f'{pos}{zhi}{names[rs]}' + (f'+{pts}' if pts else ''))
    di_score = min(di_score, 5)
    details.append(('得地', di_score, '；'.join(di_parts)))

    shi_score = 0
    shi_parts = []
    for pos, key in [('年干', 'year'), ('月干', 'month'), ('时干', 'hour')]:
        g = pillars[key][0]
        ss = SHISHEN_TABLE[(day_gan, g)]
        if ss in ('比肩', '劫财'):
            shi_score += 2; shi_parts.append(f'{pos}{g}({ss})帮身+2')
        elif ss in ('正印', '偏印'):
            shi_score += 2; shi_parts.append(f'{pos}{g}({ss})生身+2')
        elif ss in ('正官', '七杀'):
            shi_score -= 1; shi_parts.append(f'{pos}{g}({ss})克身-1')
        elif ss in ('食神', '伤官'):
            shi_score -= 1; shi_parts.append(f'{pos}{g}({ss})泄身-1')
        else:
            shi_score -= 1; shi_parts.append(f'{pos}{g}({ss})耗身-1')
    details.append(('得势', shi_score, '；'.join(shi_parts)))

    total = ling_score + di_score + shi_score
    if total >= 9:
        level, level_name = 5, '很强'
    elif total >= 6:
        level, level_name = 4, '偏强'
    elif total >= 3:
        level, level_name = 3, '中和'
    elif total >= 0:
        level, level_name = 2, '偏弱'
    else:
        level, level_name = 1, '很弱'

    return {'level': level, 'level_name': level_name, 'score': total,
            'details': [{'name': n, 'score': s, 'desc': d} for n, s, d in details]}

# ── 喜用神 ───────────────────────────────────────────────
def determine_yongshen(pillars, strength):
    day_gan = pillars['day'][0]
    dwx = GAN_WX[day_gan]
    month_zhi = pillars['month'][1]
    season = SEASON_BY_ZHI[month_zhi]
    level = strength['level']
    reasons = []

    wx_count = {'木': 0.0, '火': 0.0, '土': 0.0, '金': 0.0, '水': 0.0}
    for gz in pillars.values():
        wx_count[GAN_WX[gz[0]]] += 1.0
        cg = CANG_GAN[gz[1]]
        for i, c in enumerate(cg):
            wx_count[GAN_WX[c]] += 0.6 if i == 0 else 0.3

    cong = None
    help_wx = wx_count[dwx] + wx_count[WX_SHENG_WO[dwx]]
    drain_wx = wx_count[WX_SHENG[dwx]] + wx_count[WX_KE[dwx]] + wx_count[WX_KE_WO[dwx]]
    if level == 5 and drain_wx < 1.0:
        cong = '从强'
    elif level == 1 and help_wx < 1.0:
        cong = '从弱'

    tiaohou = None
    if season == '冬' and wx_count['火'] < 1.5:
        tiaohou = '火'
        reasons.append(f'生于{month_zhi}月冬季，天寒地冻，需火调候暖局')
    elif season == '夏' and wx_count['水'] < 1.5:
        tiaohou = '水'
        reasons.append(f'生于{month_zhi}月夏季，火炎土燥，需水调候润局')

    if cong == '从强':
        yong, xi, ji = dwx, WX_SHENG_WO[dwx], WX_KE[dwx]
        reasons.append('日主极强且克泄无力，成从强格：顺其旺势，喜生扶忌克泄')
    elif cong == '从弱':
        yong = WX_KE[dwx] if wx_count[WX_KE[dwx]] >= wx_count[WX_SHENG[dwx]] else WX_SHENG[dwx]
        xi, ji = WX_KE[dwx], dwx
        reasons.append('日主极弱且生扶无力，成从弱格：弃命从势，喜克泄耗忌生扶')
    elif level >= 4:
        yong, xi, ji = WX_KE_WO[dwx], WX_SHENG[dwx], dwx
        reasons.append(f'日主{day_gan}{dwx}偏强，强则抑之：取克我之{yong}(官杀)为用，我生之{xi}(食伤)为喜')
    elif level <= 2:
        yong, xi, ji = WX_SHENG_WO[dwx], dwx, WX_KE[dwx]
        reasons.append(f'日主{day_gan}{dwx}偏弱，弱则扶之：取生我之{yong}(印星)为用，同类{xi}(比劫)为喜')
    else:
        if tiaohou:
            yong = tiaohou
            xi = WX_SHENG[yong] if WX_SHENG[yong] != dwx else dwx
            ji = WX_KE[yong]
            reasons.append('日主中和，以调候为要，寒暖燥湿得宜为贵')
        else:
            yong, xi, ji = WX_SHENG[dwx], dwx, WX_KE_WO[dwx]
            reasons.append('日主中和，取食伤泄秀为用，维持五行流通')

    if tiaohou and not cong and tiaohou != yong and tiaohou != xi:
        xi = tiaohou
        reasons.append(f'兼取{tiaohou}调候为喜神')

    ji_list = [ji]
    for w in ['木', '火', '土', '金', '水']:
        if w != yong and w != xi and w not in ji_list and WX_KE[w] == yong:
            ji_list.append(w)
            break
    if len(ji_list) < 2:
        for w in ['木', '火', '土', '金', '水']:
            if w != yong and w != xi and w not in ji_list:
                ji_list.append(w)
                break

    return {'yongshen': yong, 'xishen': xi, 'jishen': ji_list, 'tiaohou': tiaohou,
            'cong': cong, 'reasons': reasons,
            'wx_count': {k: round(v, 1) for k, v in wx_count.items()}}

def daily_advice(ys):
    yong, xi, ji = ys['yongshen'], ys['xishen'], ys['jishen']
    good = [yong] + ([xi] if xi != yong else [])
    bad = ji[:2]
    return {
        'lucky_colors': sum([WX_INFO[w]['colors'] for w in good], []),
        'avoid_colors': sum([WX_INFO[w]['colors'] for w in bad], []),
        'lucky_dir': [WX_INFO[w]['dir'] for w in good],
        'avoid_dir': [WX_INFO[w]['dir'] for w in bad],
        'lucky_nums': sorted(set(sum([WX_INFO[w]['nums'] for w in good], []))),
        'avoid_nums': sorted(set(sum([WX_INFO[w]['nums'] for w in bad], []))),
        'lucky_season': [WX_INFO[w]['season'] for w in good],
        'good_industry': sum([WX_INFO[w]['industry'] for w in good], []),
        'avoid_industry': sum([WX_INFO[w]['industry'] for w in bad], []),
        'health_focus': [f'{WX_INFO[w]["organs"]}系统宜温养' for w in good],
        'health_warn': [f'{WX_INFO[w]["organs"]}系统需留意' for w in bad],
        'accessory': WX_INFO[yong]['accessory'],
    }

# ── 十神与重点分析 ────────────────────────────────────────
def ten_gods_analysis(pillars, day_gan):
    stats = {}
    items = []
    for pos, key in [('年干', 'year'), ('月干', 'month'), ('时干', 'hour')]:
        g = pillars[key][0]
        ss = SHISHEN_TABLE[(day_gan, g)]
        stats[ss] = stats.get(ss, 0) + 1
        items.append({'pos': pos, 'char': g, 'shishen': ss})
    for pos, key in [('年支', 'year'), ('月支', 'month'), ('日支', 'day'), ('时支', 'hour')]:
        zhi = pillars[key][1]
        cg = CANG_GAN[zhi][0]
        ss = '日主本气' if key == 'day' and zhi == pillars['day'][1] and cg == day_gan else SHISHEN_TABLE[(day_gan, cg)]
        stats[ss] = stats.get(ss, 0) + 1
        items.append({'pos': pos, 'char': f'{zhi}(藏{cg})', 'shishen': ss})
    return stats, items

def focus_analysis(focus_ids, pillars, strength, ys, dayun, gender, ten_stats):
    day_gan = pillars['day'][0]
    dwx = GAN_WX[day_gan]
    day_zhi = pillars['day'][1]
    yong, xi = ys['yongshen'], ys['xishen']
    level_name = strength['level_name']
    kw_map = {k['id']: k for k in DEFAULT_FOCUS_KEYWORDS}
    out = []

    if 'career' in focus_ids:
        cai = ten_stats.get('正财', 0) + ten_stats.get('偏财', 0)
        guan = ten_stats.get('正官', 0) + ten_stats.get('七杀', 0)
        txt = f'日主{day_gan}{dwx}，{level_name}。'
        if guan > 0 and cai > 0:
            txt += '命中财官双现，适合管理岗或体制内发展，财生官旺，事业有台阶可上。'
        elif cai > 0:
            txt += '财星显现，对钱财机会敏感，适合经商、销售、金融等求财路线。'
        elif guan > 0:
            txt += '官星显现，有责任感和纪律性，适合公职、管理、法律等路线。'
        else:
            txt += '财官不显，宜以专业技术立身，靠食伤生财（技术变现、创作输出）求发展。'
        txt += f'用神为{yong}，行{yong}运及{WX_INFO[yong]["season"]}事业机会较多；有利行业：{"、".join(WX_INFO[yong]["industry"][:4])}等；发展方位宜{WX_INFO[yong]["dir"]}。'
        out.append({'title': kw_map['career']['name'], 'icon': '💼', 'text': txt})

    if 'marriage' in focus_ids:
        spouse_star = '财星' if gender == '男' else '官星'
        ss_key = ('正财', '偏财') if gender == '男' else ('正官', '七杀')
        n = sum(ten_stats.get(k, 0) for k in ss_key)
        txt = f'日支（夫妻宫）为{day_zhi}，藏{"/".join(CANG_GAN[day_zhi])}。'
        if n == 0:
            txt += f'{spouse_star}不显，感情缘分偏淡或晚婚为宜，宜主动经营。'
        elif n == 1:
            txt += f'{spouse_star}一位清纯，感情专一，婚姻稳定度较好。'
        else:
            txt += f'{spouse_star}多位，感情选择多但易生波折，婚前宜多观察。'
        txt += f'配偶宜五行喜{yong}之人，方位{WX_INFO[yong]["dir"]}者为佳。'
        out.append({'title': kw_map['marriage']['name'], 'icon': '💞', 'text': txt})

    if 'health' in focus_ids:
        wx_weak = min(ys['wx_count'], key=lambda k: ys['wx_count'][k])
        wx_strong = max(ys['wx_count'], key=lambda k: ys['wx_count'][k])
        txt = f'命中{wx_strong}气偏旺、{wx_weak}气偏弱。{WX_INFO[wx_weak]["organs"]}系统为薄弱环节，日常宜温养；{WX_INFO[wx_strong]["organs"]}系统虽旺亦防过亢。'
        txt += f'喜用为{yong}，可多接触{WX_INFO[yong]["colors"][0]}系，饮食{WX_INFO[yong]["taste"]}味适度，佩戴{WX_INFO[yong]["accessory"]}有助气场。'
        out.append({'title': kw_map['health']['name'], 'icon': '🏥', 'text': txt})

    if 'study' in focus_ids:
        yin = ten_stats.get('正印', 0) + ten_stats.get('偏印', 0)
        shi = ten_stats.get('食神', 0) + ten_stats.get('伤官', 0)
        txt = ''
        if yin > 0:
            txt += f'印星{"旺" if yin > 1 else "现"}，记忆力与理解力佳，适合学历路线、考证深造。'
        if shi > 0:
            txt += f'食伤{"旺" if shi > 1 else "现"}，思维活跃有才华，适合创意、技术、表达类学科。'
        if not txt:
            txt += '印星食伤不显，学习宜以实践出真知，职业教育、技能路线更有优势。'
        txt += f'考试有利方位{WX_INFO[yong]["dir"]}，书桌宜朝{WX_INFO[yong]["dir"]}，文具多用{WX_INFO[yong]["colors"][0]}系。'
        out.append({'title': kw_map['study']['name'], 'icon': '📚', 'text': txt})

    if 'social' in focus_ids:
        bi = ten_stats.get('比肩', 0) + ten_stats.get('劫财', 0)
        txt = f'比劫{"旺" if bi > 1 else ("现" if bi else "弱")}，'
        txt += '朋友缘厚社交圈广，但合伙求财需谨慎账目。' if bi > 1 else ('人际平和，贵人多在长辈上司。' if bi == 0 else '有同辈助力，合作运尚可。')
        txt += f'贵人方位：{WX_INFO[yong]["dir"]}、{WX_INFO[xi]["dir"]}；多亲近五行{yong}旺之人（生于{WX_INFO[yong]["season"]}者）。'
        out.append({'title': kw_map['social']['name'], 'icon': '🤝', 'text': txt})

    if 'personality' in focus_ids:
        traits = {'木': '仁厚正直，有上进心和恻隐之心，性格偏直', '火': '热情开朗，行动力强，重礼节但性急',
                  '土': '诚信稳重，包容务实，但易固执', '金': '果决刚毅，讲义气重原则，但易强硬',
                  '水': '聪慧机敏，善变通有谋略，但易多思'}
        txt = f'日主{day_gan}为{GAN_YY[day_gan]}{dwx}，{traits[dwx]}。{level_name}：'
        if strength['level'] >= 4:
            txt += '自我意识强，有主见能扛事，宜修谦和。'
        elif strength['level'] <= 2:
            txt += '为人随和敏感，善察言观色，宜增自信。'
        else:
            txt += '性格中和，进退有度，适应力强。'
        txt += f'喜用{yong}：日常多亲近{WX_INFO[yong]["colors"][0]}系与{WX_INFO[yong]["dir"]}方位，心态运势更顺遂。'
        out.append({'title': kw_map['personality']['name'], 'icon': '🧭', 'text': txt})

    if 'luck' in focus_ids:
        dl = (dayun or {}).get('dayun_list', [])
        now_year = datetime.now().year
        cur, nxt = None, None
        for d in dl:
            yrs = str(d.get('year_range', '')).split('-')
            if len(yrs) == 2:
                try:
                    y0, y1 = int(yrs[0]), int(yrs[1])
                    if y0 <= now_year <= y1:
                        cur = d
                    elif y0 > now_year and nxt is None:
                        nxt = d
                except Exception:
                    pass
        txt = f'起运：{(dayun or {}).get("start_age_description", "-")}（{(dayun or {}).get("direction", "")}）。'
        if cur:
            g = cur['gan_zhi'][0]
            gwx = GAN_WX[g]
            if gwx == yong:
                rel = '用神到位，十年运势顺遂，宜大胆进取'
            elif gwx == xi:
                rel = '喜神辅运，平稳中有进益'
            elif gwx in ys['jishen']:
                rel = '忌神当值，宜守不宜攻，谨慎投资变动'
            else:
                rel = '平运阶段，随遇而安'
            txt += f'当前大运{cur["gan_zhi"]}（{cur.get("age_range_xu", "")}，{cur.get("year_range", "")}），{g}{gwx}：{rel}。'
        if nxt:
            txt += f'下步大运{nxt["gan_zhi"]}（{nxt.get("year_range", "")}）。'
        txt += f'凡{yong}、{xi}五行之年月多有机遇；忌神{ys["jishen"][0]}之年宜静守。'
        out.append({'title': kw_map['luck']['name'], 'icon': '🎋', 'text': txt})

    return out

# ── 主分析入口 ───────────────────────────────────────────
def analyze(data):
    name = (data.get('name') or '').strip() or '未命名'
    gender = data.get('gender', '男')
    calendar = data.get('calendar', 'solar')
    y, m, d = int(data['year']), int(data['month']), int(data['day'])
    hour = int(data.get('hour', 12))
    minute = int(data.get('minute', 0))
    city = (data.get('city') or '北京').strip()
    is_leap = bool(data.get('lunar_leap'))
    focus = data.get('focus') or []

    lunar_display = None
    if calendar == 'lunar':
        sd = lunar_to_solar(y, m, d, is_leap)
        sy, sm, sday = sd.year, sd.month, sd.day
        lunar_display = f'农历{"闰" if is_leap else ""}{LUNAR_MONTH_CN[m]}{lunar_day_cn(d)}'
    else:
        sy, sm, sday = y, m, d

    chart = compute_chart(sy, sm, sday, hour, minute, gender, city)
    gz = chart['lunar']['gan_zhi']
    pillars = {'year': gz['year'], 'month': gz['month'], 'day': gz['day'], 'hour': gz['hour']}
    day_gan = pillars['day'][0]

    strength = judge_strength(pillars)
    ys = determine_yongshen(pillars, strength)
    adv = daily_advice(ys)
    ten_stats, ten_items = ten_gods_analysis(pillars, day_gan)

    # 深度分析引擎（参考专业命书结构：流年表/月度节奏/行业梯队/格局主线）
    import bazi_deep
    ctx = dict(pillars=pillars, strength=strength, ys=ys, adv=adv, ten_stats=ten_stats,
               dayun=chart.get('dayun', {}), gender=gender, day_gan=day_gan, birth_year=sy)
    focus_out = bazi_deep.build_focus_blocks(focus, ctx)
    # 总结章始终输出（补运+心法+校准点）
    focus_out.append(bazi_deep.build_summary(pillars, strength, ys, adv, ten_stats,
                                             chart.get('dayun', {}), gender, day_gan, sy))

    li = chart['lunar']['date']
    return {
        'ok': True, 'name': name, 'gender': gender, 'city': city,
        'birth_solar': f'{sy}-{sm:02d}-{sday:02d} {hour:02d}:{minute:02d}',
        'birth_lunar': lunar_display or f'农历{li.get("month_cn", "")}{"闰" if li.get("is_leap_month") else ""}{li.get("day_cn") or lunar_day_cn(li.get("day", 1))} {li.get("shichen", "")}时',
        'pillars': pillars,
        'four_pillars_text': gz.get('four_pillars', ''),
        'day_master': f'{day_gan}{GAN_WX[day_gan]}（{GAN_YY[day_gan]}）',
        'jieqi': chart['lunar'].get('jieqi', {}),
        'strength': strength, 'yongshen': ys, 'advice': adv,
        'ten_gods': {'stats': ten_stats, 'items': ten_items},
        'dayun': chart.get('dayun', {}),
        'focus': focus_out,
    }

# ── 存储 ─────────────────────────────────────────────────
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS bazi_records(
        id TEXT PRIMARY KEY, name TEXT, gender TEXT, city TEXT,
        birth TEXT, calendar TEXT, input_json TEXT, result_json TEXT,
        created_at TEXT)''')
    return conn

def save_record(rec_id, data, result):
    with _lock:
        conn = _conn()
        old = conn.execute('SELECT created_at FROM bazi_records WHERE id=?', (rec_id,)).fetchone()
        created = old[0] if old else datetime.now().strftime('%Y-%m-%d %H:%M')
        conn.execute('INSERT OR REPLACE INTO bazi_records(id,name,gender,city,birth,calendar,input_json,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (
            rec_id, data.get('name', ''), data.get('gender', ''), data.get('city', ''),
            f"{data.get('year')}-{data.get('month')}-{data.get('day')} {data.get('hour', 0)}:{data.get('minute', 0)}",
            data.get('calendar', 'solar'), json.dumps(data, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False), created))
        conn.commit()
        conn.close()
    return {'ok': True, 'id': rec_id}

def list_records():
    with _lock:
        conn = _conn()
        rows = conn.execute('SELECT id,name,gender,city,birth,calendar,created_at FROM bazi_records ORDER BY created_at DESC LIMIT 200').fetchall()
        conn.close()
    return {'ok': True, 'records': [{'id': r[0], 'name': r[1], 'gender': r[2], 'city': r[3],
                                     'birth': r[4], 'calendar': r[5], 'created_at': r[6]} for r in rows]}

def get_record(rec_id):
    with _lock:
        conn = _conn()
        row = conn.execute('SELECT input_json,result_json FROM bazi_records WHERE id=?', (rec_id,)).fetchone()
        conn.close()
    if not row:
        return {'ok': False, 'error': '记录不存在'}
    return {'ok': True, 'input': json.loads(row[0]), 'result': json.loads(row[1])}

def delete_record(rec_id):
    with _lock:
        conn = _conn()
        conn.execute('DELETE FROM bazi_records WHERE id=?', (rec_id,))
        conn.commit()
        conn.close()
    return {'ok': True}

def get_focus_keywords(db_load, db_save):
    val = db_load('bazi_focus_keywords')
    if not val:
        val = DEFAULT_FOCUS_KEYWORDS
        db_save('bazi_focus_keywords', val)
    return {'ok': True, 'keywords': val}

def save_focus_keywords(db_save, kws):
    db_save('bazi_focus_keywords', kws)
    return {'ok': True}
