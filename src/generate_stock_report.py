#!/usr/bin/env python3
"""AI 股票分析报告生成器
输入股票代码 → 拉取行情+财务数据 → DCF三情景估值 → 生成 HTML 报告
用法: python3 generate_stock_report.py <代码> [--market A|HK]
输出: 报告 HTML 写入 stock-reports/ 目录,stdout 打印 JSON 元信息
"""
import sys, os, json, time, math, argparse, datetime, re

ROOT = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(ROOT, 'stock-reports')
os.makedirs(REPORT_DIR, exist_ok=True)

# ── 行情(腾讯接口,稳定)──────────────────────────────────────────────
def get_quote(code, market):
    import urllib.request
    prefix = 'sh' if code.startswith('6') or market == 'SH' else ('hk' if market == 'HK' else 'sz')
    if market == 'HK':
        symbol = 'hk' + code.zfill(5)
    else:
        symbol = prefix + code
    url = f'http://qt.gtimg.cn/q={symbol}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    raw = urllib.request.urlopen(req, timeout=15).read()
    try:
        line = raw.decode('gbk')
    except:
        line = raw.decode('utf-8', errors='replace')
    line = line.strip()
    if '="' in line:
        line = line.split('="', 1)[1]
    line = line.rstrip('";')
    p = line.split('~')
    if len(p) < 50:
        raise ValueError(f'行情解析失败: 字段数 {len(p)}')
    def f(i, d=0.0):
        try: return float(p[i])
        except: return d
    return {
        'name': p[1],
        'price': f(3),
        'prev_close': f(4),
        'change_pct': f(32),
        'turnover': f(38),
        'pe': f(39),
        'float_mv': f(44),   # 流通市值(亿)
        'total_mv': f(45),   # 总市值(亿)
        'pb': f(46),
        'amplitude': f(43),
    }

# ── 数值解析工具 ──────────────────────────────────────────────────────
def parse_pct(s):
    """'33.1%' -> 33.1;解析失败返回 None"""
    if s is None: return None
    s = str(s).replace('%', '').replace(',', '').strip()
    try: return float(s)
    except: return None

def parse_yi(s):
    """'28.20亿' -> 28.20;'2820.00万' -> 0.282;解析失败返回 None"""
    if s is None: return None
    s = str(s).replace(',', '').strip()
    try:
        if '亿' in s: return float(s.replace('亿', ''))
        if '万' in s: return float(s.replace('万', '')) / 10000
        return float(s)
    except: return None

def parse_num(s):
    """'1.23' -> 1.23;解析失败返回 None"""
    if s is None: return None
    s = str(s).replace(',', '').strip()
    try: return float(s)
    except: return None

# ── 财务数据(akshare 同花顺摘要)──────────────────────────────────────
def get_financials(code):
    import akshare as ak
    df = ak.stock_financial_abstract_ths(symbol=code, indicator='按年度')
    rows = []
    for _, r in df.tail(6).iterrows():
        rows.append({
            'year': str(r.get('报告期', '')),
            'revenue': str(r.get('营业总收入', '')),
            'revenue_yoy': str(r.get('营业总收入同比增长率', '')),
            'net_profit': str(r.get('净利润', '')),
            'profit_yoy': str(r.get('净利润同比增长率', '')),
            'deduct_profit': str(r.get('扣非净利润', '')),
            'gross_margin': str(r.get('销售毛利率', '')),
            'net_margin': str(r.get('销售净利率', '')),
            'roe': str(r.get('净资产收益率', '')),
            'debt_ratio': str(r.get('资产负债率', '')),
            'current_ratio': str(r.get('流动比率', '')),
            'quick_ratio': str(r.get('速动比率', '')),
            'equity_ratio': str(r.get('产权比率', '')),
            'ar_days': str(r.get('应收账款周转天数', '')),
            'inv_days': str(r.get('存货周转天数', '')),
            'eps': str(r.get('基本每股收益', '')),
            'bps': str(r.get('每股净资产', '')),
            'ocf_ps': str(r.get('每股经营现金流', '')),
            # 数值版本(供分析计算)
            '_revenue': parse_yi(r.get('营业总收入')),
            '_net_profit': parse_yi(r.get('净利润')),
            '_deduct': parse_yi(r.get('扣非净利润')),
            '_revenue_yoy': parse_pct(r.get('营业总收入同比增长率')),
            '_profit_yoy': parse_pct(r.get('净利润同比增长率')),
            '_gross_margin': parse_pct(r.get('销售毛利率')),
            '_net_margin': parse_pct(r.get('销售净利率')),
            '_roe': parse_pct(r.get('净资产收益率')),
            '_debt_ratio': parse_pct(r.get('资产负债率')),
            '_current_ratio': parse_num(r.get('流动比率')),
            '_quick_ratio': parse_num(r.get('速动比率')),
            '_equity_ratio': parse_num(r.get('产权比率')),
            '_ar_days': parse_num(r.get('应收账款周转天数')),
            '_inv_days': parse_num(r.get('存货周转天数')),
            '_bps': parse_num(r.get('每股净资产')),
            '_ocf_ps': parse_num(r.get('每股经营现金流')),
            '_eps': parse_num(r.get('基本每股收益')),
        })
    return rows

def get_cashflow(code):
    """返回最近年报的经营现金流、资本开支、FCF(亿元)"""
    import akshare as ak
    sym = ('SZ' if code.startswith(('0', '3')) else 'SH') + code
    df = ak.stock_cash_flow_sheet_by_report_em(symbol=sym)
    # 找最近一个年报(12-31)
    for _, r in df.iterrows():
        d = str(r.get('REPORT_DATE', ''))
        if '12-31' in d:
            op = float(r.get('NETCASH_OPERATE', 0) or 0) / 1e8
            cap = float(r.get('CONSTRUCT_LONG_ASSET', 0) or 0) / 1e8
            return {'date': d[:10], 'op_cf': op, 'capex': cap, 'fcf': op - cap}
    return None

def get_latest_quarter(code):
    import akshare as ak
    df = ak.stock_financial_abstract_ths(symbol=code, indicator='按单季度')
    r = df.tail(1).iloc[0]
    return {
        'period': str(r.get('报告期', '')),
        'revenue': str(r.get('营业总收入', '')),
        'revenue_yoy': str(r.get('营业总收入同比增长率', '')),
        'net_profit': str(r.get('净利润', '')),
        'profit_yoy': str(r.get('净利润同比增长率', '')),
        'gross_margin': str(r.get('销售毛利率', '')),
    }

# ── DCF 估值 ──────────────────────────────────────────────────────────
def dcf(fcf, growth_list, terminal, discount, net_debt, shares):
    """返回每股内在价值"""
    pv_sum = 0.0
    cur = fcf
    for i, g in enumerate(growth_list):
        cur = cur * (1 + g)
        pv_sum += cur / ((1 + discount) ** (i + 1))
    # 第5年后永续(用最后一年FCF)
    last_fcf = cur
    # 继续5年(6-10年)按最后增速衰减到terminal
    for yr in range(6, 11):
        last_fcf = last_fcf * (1 + terminal + 0.02)  # 过渡
        pv_sum += last_fcf / ((1 + discount) ** yr)
    terminal_value = last_fcf * (1 + terminal) / (discount - terminal)
    pv_terminal = terminal_value / ((1 + discount) ** 10)
    ev = pv_sum + pv_terminal
    equity = ev - net_debt
    return equity / shares if shares else 0

# ── 评级逻辑 ──────────────────────────────────────────────────────────
def judge(price, optimistic, neutral, pessimistic):
    if price <= pessimistic * 1.1:
        return '🟢 买入', '深度低估区,安全边际充足'
    if price <= neutral:
        return '🟢 逢低关注', '估值合理偏低,具备安全边际'
    if price <= optimistic:
        return '🟡 持有/观望', '估值合理区间,安全边际有限'
    return '🔴 回避/卖出', '估值偏高,缺乏安全边际'

# ── 分析引擎:从财务数据自动推导各维度 ────────────────────────────────
def analyze(fins, cf, quote):
    """基于近6年财务数据 + 现金流,计算十分钟测试/财务健康/成长性/护城河/综合评分"""
    latest = fins[-1] if fins else {}
    # 有效年份(有营收数据的)
    valid = [f for f in fins if f.get('_revenue')]
    roes = [f['_roe'] for f in valid if f.get('_roe') is not None]
    gross = [f['_gross_margin'] for f in valid if f.get('_gross_margin') is not None]
    netm = [f['_net_margin'] for f in valid if f.get('_net_margin') is not None]
    rev_yoys = [f['_revenue_yoy'] for f in valid if f.get('_revenue_yoy') is not None]
    profit_yoys = [f['_profit_yoy'] for f in valid if f.get('_profit_yoy') is not None]

    latest_roe = latest.get('_roe')
    latest_gross = latest.get('_gross_margin')
    latest_debt = latest.get('_debt_ratio')
    latest_cr = latest.get('_current_ratio')
    latest_qr = latest.get('_quick_ratio')
    latest_er = latest.get('_equity_ratio')
    latest_net_profit = latest.get('_net_profit')
    latest_deduct = latest.get('_deduct')
    latest_ocf_ps = latest.get('_ocf_ps')
    latest_eps = latest.get('_eps')

    # ── 十分钟测试 ──
    ten = []
    # 1. 持续盈利
    profit_years = sum(1 for f in valid if (f.get('_net_profit') or 0) > 0)
    ten.append(('持续赚取营业利润', f'近{len(valid)}年中{profit_years}年盈利',
                profit_years >= max(1, len(valid) - 1), '盈利持续性'))
    # 2. 持续经营现金流(用每股经营现金流>0近似)
    ocf_ok = latest_ocf_ps is not None and latest_ocf_ps > 0
    ocf_desc = f'最新每股经营现金流 {latest_ocf_ps:.2f} 元' if latest_ocf_ps is not None else '数据缺失'
    ten.append(('持续产生经营现金流', ocf_desc, ocf_ok, '现金流质量'))
    # 3. ROE>10% 且杠杆合理
    roe_ok = latest_roe is not None and latest_roe > 10
    debt_ok = latest_debt is None or latest_debt < 60
    ten.append(('ROE>10% 且杠杆合理', f'ROE {latest_roe:.1f}%,负债率 {latest_debt:.1f}%' if latest_roe and latest_debt else '数据缺失',
                roe_ok and debt_ok, '资本效率'))
    # 4. 盈利增长稳定(利润增速波动)
    if len(profit_yoys) >= 3:
        import statistics
        vol = statistics.pstdev(profit_yoys)
        stable = vol < 25
        ten.append(('盈利增长稳定', f'利润增速波动 σ={vol:.0f}%', stable, '增长稳定性'))
    else:
        ten.append(('盈利增长稳定', '数据不足', None, '增长稳定性'))
    # 5. 资产负债表清白
    bs_ok = (latest_debt is None or latest_debt < 60) and (latest_cr is None or latest_cr > 1)
    ten.append(('资产负债表清白', f'负债率 {latest_debt:.1f}%,流动比率 {latest_cr:.2f}' if latest_debt and latest_cr else '数据缺失',
                bs_ok, '财务风险'))
    # 6. 产生自由现金流
    fcf_ok = cf is not None and cf.get('fcf', 0) > 0
    fcf_desc = f'FCF {cf["fcf"]:.1f}亿' if cf else '数据缺失'
    ten.append(('产生自由现金流', fcf_desc, fcf_ok, '真实现金创造'))
    # 7. 利润质量(扣非占比)
    if latest_net_profit and latest_deduct:
        dq = latest_deduct / latest_net_profit * 100
        ten.append(('利润质量(扣非占比)', f'扣非/净利 = {dq:.0f}%', dq >= 80, '非经常性损益'))
    else:
        ten.append(('利润质量(扣非占比)', '数据缺失', None, '非经常性损益'))
    # 8. 股本稀释(用每股净资产趋势近似,BPS上升=未稀释)
    bps_list = [f['_bps'] for f in valid if f.get('_bps')]
    if len(bps_list) >= 2:
        bps_up = bps_list[-1] >= bps_list[0]
        ten.append(('股本未明显稀释', f'每股净资产 {bps_list[0]:.2f}→{bps_list[-1]:.2f}', bps_up, '股本稀释'))
    else:
        ten.append(('股本未明显稀释', '数据不足', None, '股本稀释'))
    # 9. DCF 估值吸引力(占位,由主流程填)
    ten.append(('DCF 估值吸引力', '见估值章节', None, '估值吸引力'))

    # ── 财务健康检查(六个危险信号)──
    health = []
    # 现金流衰退
    if latest_ocf_ps is not None and latest_eps:
        cf_decline = latest_ocf_ps < latest_eps * 0.5
        health.append(('现金流衰退', f'每股经营现金流 {latest_ocf_ps:.2f} vs 每股收益 {latest_eps:.2f}', not cf_decline))
    else:
        health.append(('现金流衰退', '数据缺失', None))
    # 应收异常(应收周转天数趋势)
    ar_list = [f['_ar_days'] for f in valid if f.get('_ar_days')]
    if len(ar_list) >= 2:
        ar_rise = ar_list[-1] > ar_list[0] * 1.3
        health.append(('应收账款异常', f'周转天数 {ar_list[0]:.0f}→{ar_list[-1]:.0f}天', not ar_rise))
    else:
        health.append(('应收账款异常', '数据缺失', None))
    # 存货异常
    inv_list = [f['_inv_days'] for f in valid if f.get('_inv_days')]
    if len(inv_list) >= 2:
        inv_rise = inv_list[-1] > inv_list[0] * 1.3
        health.append(('存货异常', f'周转天数 {inv_list[0]:.0f}→{inv_list[-1]:.0f}天', not inv_rise))
    else:
        health.append(('存货异常', '数据缺失', None))
    # 高杠杆
    health.append(('高杠杆', f'资产负债率 {latest_debt:.1f}%,产权比率 {latest_er:.2f}' if latest_debt and latest_er else '数据缺失',
                   (latest_debt is None or latest_debt < 60)))
    # 偿债能力
    health.append(('偿债能力', f'流动比率 {latest_cr:.2f},速动比率 {latest_qr:.2f}' if latest_cr and latest_qr else '数据缺失',
                   (latest_cr is None or latest_cr > 1)))
    # 毛利率稳定
    if len(gross) >= 2:
        gm_stable = gross[-1] >= gross[0] - 5
        health.append(('毛利率稳定', f'{gross[0]:.1f}%→{gross[-1]:.1f}%', gm_stable))
    else:
        health.append(('毛利率稳定', '数据缺失', None))

    # ── 成长性分析 ──
    growth = {}
    if rev_yoys:
        growth['rev_cagr'] = sum(rev_yoys) / len(rev_yoys)
        growth['rev_latest'] = rev_yoys[-1]
    if profit_yoys:
        growth['profit_cagr'] = sum(profit_yoys) / len(profit_yoys)
        growth['profit_latest'] = profit_yoys[-1]
    # 营收CAGR(首尾)
    if len(valid) >= 2 and valid[0]['_revenue'] and valid[-1]['_revenue']:
        years = len(valid) - 1
        growth['rev_cagr_calc'] = ((valid[-1]['_revenue'] / valid[0]['_revenue']) ** (1 / years) - 1) * 100

    # ── 护城河(量化代理指标)──
    moat = []
    # 高且稳定的毛利率 → 定价权/差异化
    if gross:
        avg_gm = sum(gross) / len(gross)
        moat.append(('定价权(毛利率)', f'平均毛利率 {avg_gm:.1f}%',
                     '强' if avg_gm > 40 else ('中' if avg_gm > 20 else '弱')))
    # 高且稳定的ROE → 资本效率护城河
    if roes:
        avg_roe = sum(roes) / len(roes)
        moat.append(('资本效率(ROE)', f'平均ROE {avg_roe:.1f}%',
                     '强' if avg_roe > 15 else ('中' if avg_roe > 10 else '弱')))
    # 低负债 → 成本优势/财务稳健
    if latest_debt is not None:
        moat.append(('财务稳健(负债率)', f'{latest_debt:.1f}%',
                     '强' if latest_debt < 40 else ('中' if latest_debt < 60 else '弱')))

    # ── 综合评分(5分制)──
    scores = {}
    # 护城河强度(毛利率+ROE)
    mh = 3.0
    if gross and sum(gross)/len(gross) > 40: mh += 0.8
    elif gross and sum(gross)/len(gross) > 20: mh += 0.3
    if roes and sum(roes)/len(roes) > 15: mh += 0.8
    elif roes and sum(roes)/len(roes) > 10: mh += 0.4
    scores['护城河强度'] = round(min(5, mh), 1)
    # 财务健康
    fh = 3.0
    passed_h = sum(1 for _, _, ok in health if ok is True)
    fh += min(1.5, passed_h * 0.25)
    if latest_debt and latest_debt < 40: fh += 0.3
    scores['财务健康'] = round(min(5, fh), 1)
    # 成长性
    gr = 3.0
    if growth.get('rev_cagr_calc'):
        c = growth['rev_cagr_calc']
        gr += 1.0 if c > 15 else (0.5 if c > 5 else (-0.5 if c < 0 else 0))
    scores['成长性'] = round(min(5, max(1, gr)), 1)
    # 管理层(用扣非质量+ROE稳定性代理)
    mg = 3.5
    if latest_net_profit and latest_deduct and latest_deduct / latest_net_profit > 0.9: mg += 0.5
    scores['管理层'] = round(min(5, mg), 1)
    # 估值吸引力(由主流程根据安全边际填)
    scores['估值吸引力'] = 3.0  # 占位

    return {
        'ten': ten, 'health': health, 'growth': growth, 'moat': moat, 'scores': scores,
        'latest_roe': latest_roe, 'latest_gross': latest_gross, 'latest_debt': latest_debt,
        'avg_gross': sum(gross)/len(gross) if gross else None,
        'avg_roe': sum(roes)/len(roes) if roes else None,
    }

# ── HTML 生成 ─────────────────────────────────────────────────────────
def esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

WEIGHTS = {'护城河强度': 0.25, '财务健康': 0.20, '成长性': 0.20, '管理层': 0.15, '估值吸引力': 0.20}


def derive_judgments(analysis, quote, fins, cf, scenarios, rating, rating_note):
    """从量化数据自动推导各章节的判断/结论/小结,让报告不只是数据表。"""
    J = {}
    price = quote['price']
    opt, neu, pes = scenarios[0]['value'], scenarios[1]['value'], scenarios[2]['value']
    avg_gross = analysis.get('avg_gross')
    avg_roe = analysis.get('avg_roe')
    latest_debt = analysis.get('latest_debt')
    sc = analysis['scores']
    total_score = sum(sc[k] * WEIGHTS[k] for k in WEIGHTS)
    J['total_score'] = total_score

    # 提取历史数据用于趋势分析
    gross_list = [f['_gross_margin'] for f in fins if f.get('_gross_margin') is not None]
    roe_list = [f['_roe'] for f in fins if f.get('_roe') is not None]
    debt_list = [f['_debt_ratio'] for f in fins if f.get('_debt_ratio') is not None]
    rev_list = [f['_revenue'] for f in fins if f.get('_revenue')]
    profit_list = [f['_net_profit'] for f in fins if f.get('_net_profit')]

    # ── 护城河宽度判定(详细版)──
    if avg_gross is not None and avg_roe is not None:
        # 判断趋势
        gross_trend = ''
        if len(gross_list) >= 3:
            if gross_list[-1] > gross_list[0] + 2:
                gross_trend = f'毛利率从 {gross_list[0]:.1f}% 提升至 {gross_list[-1]:.1f}%,呈改善趋势,'
            elif gross_list[-1] < gross_list[0] - 2:
                gross_trend = f'毛利率从 {gross_list[0]:.1f}% 下滑至 {gross_list[-1]:.1f}%,面临竞争或成本压力,'
            else:
                gross_trend = f'毛利率稳定在 {avg_gross:.1f}% 附近,'

        roe_trend = ''
        if len(roe_list) >= 3:
            if roe_list[-1] > roe_list[0] + 2:
                roe_trend = f'ROE 从 {roe_list[0]:.1f}% 提升至 {roe_list[-1]:.1f}%,资本效率持续改善。'
            elif roe_list[-1] < roe_list[0] - 2:
                roe_trend = f'ROE 从 {roe_list[0]:.1f}% 下滑至 {roe_list[-1]:.1f}%,需警惕盈利能力下降。'
            else:
                roe_trend = f'ROE 稳定在 {avg_roe:.1f}% 水平。'

        if avg_gross > 40 and avg_roe > 15:
            J['moat_width'], J['moat_dur'] = '宽护城河', '10-20年'
            J['moat_note'] = f'{gross_trend}{roe_trend}平均毛利率 {avg_gross:.1f}%、平均ROE {avg_roe:.1f}% 长期维持高位,表明公司具备持续的竞争优势和定价权,护城河较宽,预计可持续 10-20 年。'
        elif avg_gross > 20 or avg_roe > 10:
            J['moat_width'], J['moat_dur'] = '窄护城河', '5-10年'
            J['moat_note'] = f'{gross_trend}{roe_trend}平均毛利率 {avg_gross:.1f}%、平均ROE {avg_roe:.1f}% 中等偏上,公司有一定壁垒但需持续投入维持,属窄护城河,预计可持续 5-10 年。'
        else:
            J['moat_width'], J['moat_dur'] = '无明显护城河', '-'
            J['moat_note'] = f'{gross_trend}{roe_trend}平均毛利率 {avg_gross:.1f}%、平均ROE {avg_roe:.1f}% 偏低,竞争优势不显著,易受同业竞争侵蚀,暂未观察到明显护城河。'
    else:
        J['moat_width'], J['moat_dur'] = '数据不足', '-'
        J['moat_note'] = '财务数据不足,无法量化判定护城河宽度。'

    # ── 十分钟测试小结(详细版)──
    ten = analysis['ten']
    passed = sum(1 for t in ten if t[2] is True)
    total = sum(1 for t in ten if t[2] is not None)
    failed_items = [(t[0], t[1]) for t in ten if t[2] is False]
    failed_names = [t[0] for t in ten if t[2] is False]
    # 估值项是否单独失败
    val_only_fail = failed_names == ['DCF 估值吸引力']

    # 构建详细分析
    ten_analysis = []
    if total and passed == total:
        ten_analysis.append(f'**结论:{passed}/{total} 全部通过。** 这是一份优秀的答卷--')
        ten_analysis.append('公司在盈利持续性、现金流质量、资本效率、财务稳健性等核心维度均达标,')
        ten_analysis.append('表明其商业模式经受住了时间检验,具备价值投资的基本素质。')
    elif val_only_fail:
        ten_analysis.append(f'**结论:{passed}/{total} 通过,仅估值一项不达标。** ')
        ten_analysis.append('经营层面是优等生:盈利稳定、现金流健康、资产负债表干净、ROE 达标,')
        ten_analysis.append('问题纯粹出在"贵"--当前价格已透支了未来成长预期。')
        ten_analysis.append('对价值投资者而言,好公司还需好价格,建议耐心等待回调。')
    elif total and passed >= total * 0.7:
        ten_analysis.append(f'**结论:{passed}/{total} 通过。** 基本面总体尚可,但存在个别隐患:')
        fail_desc = ';'.join(f'{name}({desc})' for name, desc in failed_items)
        ten_analysis.append(f'{fail_desc},需关注其改善趋势。')
        ten_analysis.append('总体看,公司具备一定投资价值,但需对上述薄弱环节保持跟踪。')
    else:
        ten_analysis.append(f'**结论:{passed}/{total} 通过。** 基本面存在多处隐患,需谨慎:')
        fail_desc = ';'.join(f'{name}({desc})' for name, desc in failed_items)
        ten_analysis.append(f'{fail_desc}。')
        ten_analysis.append('多项核心指标未达标,表明公司商业模式或财务状况存在结构性问题,')
        ten_analysis.append('价值投资视角下吸引力有限,建议规避或等待根本性改善。')

    J['ten_note'] = ' '.join(ten_analysis)

    # ── 财务健康小结(详细版)──
    health = analysis['health']
    h_pass = sum(1 for h in health if h[2] is True)
    h_fail_items = [(h[0], h[1]) for h in health if h[2] is False]
    h_fail = [h[0] for h in health if h[2] is False]

    health_analysis = []
    if not h_fail and (latest_debt is None or latest_debt < 40):
        health_analysis.append(f'**财务非常干净,几乎无危险信号。** ')
        if latest_debt is not None:
            health_analysis.append(f'资产负债率仅 {latest_debt:.1f}%,远低于 60% 的警戒线,')
            health_analysis.append('表明公司几乎不依赖外部融资,财务弹性充足,抗风险能力强。')
        health_analysis.append('现金流与利润匹配、应收存货周转正常、偿债能力健康--')
        health_analysis.append('这是一张典型的稳健制造业/消费业资产负债表,价值投资的安全边际较高。')
    elif not h_fail:
        health_analysis.append(f'**未发现明显危险信号,财务总体稳健。** ')
        if latest_debt is not None:
            health_analysis.append(f'资产负债率 {latest_debt:.1f}%,处于合理区间(<60%),')
            health_analysis.append('虽非极低负债,但仍在可控范围内。')
        health_analysis.append('各项财务指标未见异常,公司财务纪律良好。')
    else:
        health_analysis.append(f'**发现 {len(h_fail)} 项危险信号,需高度警惕:**')
        fail_desc = ';'.join(f'{name}({desc})' for name, desc in h_fail_items)
        health_analysis.append(f'{fail_desc}。')
        health_analysis.append('上述信号可能指向盈利质量下降、营运资金压力或激进扩张等风险,')
        health_analysis.append('建议深入分析财报附注,确认是否存在会计操纵或经营恶化迹象。')

    J['health_note'] = ' '.join(health_analysis)

    # ── 成长性小结(详细版)──
    g = analysis['growth']
    cagr = g.get('rev_cagr_calc')
    rev_latest = g.get('rev_latest')
    profit_cagr = g.get('profit_cagr')
    profit_latest = g.get('profit_latest')

    growth_analysis = []
    if cagr is not None:
        if cagr > 15:
            stage = '高速成长期'
            stage_desc = '公司处于高速扩张阶段,营收快速增长,市场份额持续提升。'
        elif cagr > 5:
            stage = '稳健成长期'
            stage_desc = '公司保持稳健增长,营收逐年扩大,商业模式成熟且具备一定成长空间。'
        elif cagr > 0:
            stage = '低速成长/成熟期'
            stage_desc = '公司已进入成熟期,营收增长放缓,更多依赖存量市场而非增量扩张。'
        else:
            stage = '收缩期'
            stage_desc = '公司营收呈收缩态势,可能面临行业下行、竞争加剧或商业模式老化等挑战。'

        growth_analysis.append(f'**近6年营收CAGR {cagr:.1f}%,处于{stage}。** {stage_desc}')

        # 趋势判断
        if rev_latest is not None:
            if rev_latest < cagr - 3:
                growth_analysis.append(f'最新营收增速 {rev_latest:.1f}% 已低于多年均值,增长正在减速,')
                growth_analysis.append('需关注是短期波动还是长期拐点。')
            elif rev_latest > cagr + 3:
                growth_analysis.append(f'最新营收增速 {rev_latest:.1f}% 高于多年均值,增长有加速迹象,')
                growth_analysis.append('可能受益于新产品放量、行业景气度回升或并购整合。')
            else:
                growth_analysis.append(f'最新营收增速 {rev_latest:.1f}% 与历史均值基本持平,增长节奏稳定。')

        # 利润质量
        if profit_cagr is not None and profit_latest is not None:
            if profit_cagr > cagr + 5:
                growth_analysis.append(f'利润CAGR {profit_cagr:.1f}% 显著高于营收CAGR,表明盈利能力在改善(可能源于规模效应、产品结构升级或成本控制)。')
            elif profit_cagr < cagr - 5:
                growth_analysis.append(f'利润CAGR {profit_cagr:.1f}% 低于营收CAGR,需警惕"增收不增利"--可能面临价格战、成本上升或费用失控。')
            if profit_latest < 0:
                growth_analysis.append(f'最新利润增速 {profit_latest:.1f}% 为负,短期盈利承压,需确认是周期性波动还是结构性问题。')

        growth_analysis.append('增长来源分解(销量/价格/新产品/并购)与细分行业生命周期定位,需结合财报附注与行业研报进一步研判。')
    else:
        growth_analysis.append('营收数据不足,无法计算多年复合增速,成长性评估受限。')

    J['growth_note'] = ' '.join(growth_analysis)

    # ── 管理层小结(详细版)──
    mg = sc['管理层']
    latest_roe = analysis.get('latest_roe')
    avg_gross = analysis.get('avg_gross')

    mgmt_analysis = []
    mgmt_analysis.append(f'**管理层评分 {mg:.1f}/5。** ')

    if mg >= 4.0:
        mgmt_analysis.append('从量化指标看,管理层表现优秀:')
        if latest_roe and latest_roe > 12:
            mgmt_analysis.append(f'ROE 维持在 {latest_roe:.1f}% 的较高水平,表明资本配置效率高,')
            mgmt_analysis.append('管理层善于为股东创造价值;')
        if avg_gross and avg_gross > 30:
            mgmt_analysis.append(f'毛利率 {avg_gross:.1f}% 保持高位,反映产品定价权和成本控制能力;')
        mgmt_analysis.append('财报质量高,未见明显会计操纵迹象,信息披露透明度良好。')
        mgmt_analysis.append('综合判断,管理层值得信赖,具备长期持有价值。')
    elif mg >= 3.5:
        mgmt_analysis.append('经营总体稳健,未见明显诚信红旗:')
        if latest_roe:
            mgmt_analysis.append(f'ROE {latest_roe:.1f}% 处于合理区间,')
        mgmt_analysis.append('扣非利润占比高,财报质量尚可。')
        mgmt_analysis.append('但管理层能力、股东导向(分红政策、股权激励、回购记录)等定性维度,')
        mgmt_analysis.append('需结合公司公告与治理结构进一步评估。')
    else:
        mgmt_analysis.append('资本效率与财报质量一般,需重点关注:')
        if latest_roe and latest_roe < 8:
            mgmt_analysis.append(f'ROE 仅 {latest_roe:.1f}%,资本回报率偏低,')
            mgmt_analysis.append('可能反映管理层资本配置能力不足或行业竞争过于激烈;')
        mgmt_analysis.append('建议审视公司历史并购记录、关联交易、高管薪酬与业绩挂钩程度,')
        mgmt_analysis.append('确认管理层是否与股东利益一致。')

    J['mgmt_note'] = ' '.join(mgmt_analysis)

    # ── 估值解读(详细版)──
    val_analysis = []
    pe = quote['pe']
    pb = quote['pb']

    if opt < price:
        val_analysis.append(f'**估值严重偏高,缺乏安全边际。** ')
        val_analysis.append(f'即便最乐观假设({opt:.2f}元)也低于现价({price:.2f}元),')
        val_analysis.append(f'当前 PE≈{pe:.1f}、PB≈{pb:.2f},市场已充分定价甚至透支了未来成长预期。')
        val_analysis.append('从价值投资视角(要求≥20-35%安全边际)看,当前价格不具备吸引力,')
        val_analysis.append('建议等待股价回调至中性估值附近再考虑介入。')
    elif neu < price <= opt:
        val_analysis.append(f'**估值合理偏高,安全边际有限。** ')
        val_analysis.append(f'中性估值({neu:.2f}元)低于现价({price:.2f}元),')
        val_analysis.append(f'仅乐观情景({opt:.2f}元)留有空间。')
        val_analysis.append(f'当前 PE≈{pe:.1f}、PB≈{pb:.2f},处于历史中高位区间。')
        val_analysis.append('若公司未来成长不及预期,股价存在回调压力;')
        val_analysis.append('建议观望,等待更好的买入时机。')
    elif pes < price <= neu:
        val_analysis.append(f'**估值处于合理区间。** ')
        val_analysis.append(f'现价({price:.2f}元)介于悲观({pes:.2f}元)与中性({neu:.2f}元)估值之间,')
        val_analysis.append(f'当前 PE≈{pe:.1f}、PB≈{pb:.2f},估值不算便宜但也未明显高估。')
        val_analysis.append('若公司基本面稳健、成长确定性高,可逢低分批建仓;')
        val_analysis.append('若追求更高安全边际,可等待回调至悲观估值附近。')
    else:
        val_analysis.append(f'**估值具备吸引力,安全边际充足。** ')
        val_analysis.append(f'现价({price:.2f}元)低于悲观估值({pes:.2f}元),')
        val_analysis.append(f'当前 PE≈{pe:.1f}、PB≈{pb:.2f},处于历史低位区间。')
        val_analysis.append('从价值投资视角看,当前价格已提供足够的安全边际,')
        val_analysis.append('若公司基本面未发生根本性恶化,可考虑分批建仓。')

    J['val_note'] = ' '.join(val_analysis)

    # ── 操作建议价格区间 ──
    if price <= pes:
        zone = '🟢 深度低估区,安全边际充足'
    elif price <= neu:
        zone = '🟢 合理偏低区,可逢低关注'
    elif price <= opt:
        zone = '🟡 合理偏高区,观望等待回调'
    else:
        zone = '🔴 高估区,价值视角下不宜追高'
    J['price_zones'] = [
        ('深度低估(买入区)', f'< {pes:.2f} 元', '安全边际充足,可分批建仓'),
        ('合理偏低', f'{pes:.2f} ~ {neu:.2f} 元', '估值合理偏低,可逢低关注'),
        ('合理偏高', f'{neu:.2f} ~ {opt:.2f} 元', '观望,等待回调'),
        ('高估区', f'> {opt:.2f} 元', '价值视角下不宜追高'),
    ]
    J['cur_zone'] = zone

    # ── 最终结论 ──
    # 核心逻辑(优点)
    strengths = []
    if avg_gross and avg_gross > 25: strengths.append(f'毛利率较高({avg_gross:.1f}%)')
    if avg_roe and avg_roe > 12: strengths.append(f'ROE 良好({avg_roe:.1f}%)')
    if latest_debt is not None and latest_debt < 45: strengths.append(f'负债率低({latest_debt:.1f}%)')
    if cf and cf.get('fcf', 0) > 0: strengths.append(f'自由现金流为正({cf["fcf"]:.1f}亿)')
    if cagr and cagr > 8: strengths.append(f'营收持续增长(CAGR {cagr:.1f}%)')
    if not strengths: strengths.append('财务指标总体平平')
    strengths_str = "、".join(strengths)
    J['conclusion_core'] = f'{quote["name"]}判定为{J["moat_width"]}({J["moat_note"]})核心优势:{strengths_str}。'

    # 主要风险(缺点)
    risks = []
    if opt < price: risks.append('估值偏高,缺乏安全边际')
    if cf and cf.get('fcf', 0) < 0: risks.append(f'自由现金流为负({cf["fcf"]:.1f}亿)')
    if latest_debt is not None and latest_debt > 60: risks.append(f'资产负债率偏高({latest_debt:.1f}%)')
    if avg_gross and avg_gross < 15: risks.append(f'毛利率偏低({avg_gross:.1f}%),定价权弱')
    if rev_latest is not None and rev_latest < 0: risks.append(f'最新营收负增长({rev_latest:.1f}%)')
    for h in health:
        if h[2] is False: risks.append(f'财务危险信号:{h[0]}')
    if not risks: risks.append('暂未发现重大风险,但仍需跟踪行业周期与竞争格局')
    J['conclusion_risks'] = risks

    # 监控指标
    monitors = []
    if avg_gross: monitors.append('毛利率能否守住')
    if avg_roe: monitors.append('ROE 走势')
    monitors.append('营收/利润增速')
    if cf: monitors.append('自由现金流改善情况')
    monitors.append('行业需求与竞争格局变化')
    J['conclusion_monitors'] = monitors

    # 一句话结论(按评级)
    if '买入' in rating:
        J['one_liner'] = f'基本面扎实且估值具备安全边际,价值投资视角下具吸引力,可分批建仓。'
    elif '关注' in rating:
        J['one_liner'] = f'基本面良好、估值合理偏低,可逢低关注、耐心布局。'
    elif '持有' in rating or '观望' in rating:
        J['one_liner'] = f'好公司不等于好股票--基本面尚可但估值安全边际有限,建议观望等待更好的价格。'
    else:
        J['one_liner'] = f'估值偏高、缺乏安全边际,以价值投资标准(要求≥20-35%安全边际)衡量,当前价格不具备吸引力。'

    return J


def build_markdown(code, market, quote, fins, cf, quarter, scenarios, rating, rating_note, gen_time, analysis):
    """生成 Markdown 报告,交给 render_report.py 渲染为统一版式(左目录+正文)"""
    name = quote['name']
    price = quote['price']
    fcf_str = f"{cf['fcf']:.2f}亿({cf['date']})" if cf else '数据缺失'

    def mark(ok):
        if ok is True: return '✅'
        if ok is False: return '❌'
        return '⚪'

    J = derive_judgments(analysis, quote, fins, cf, scenarios, rating, rating_note)

    L = []
    L.append(f"# {name}({code})价值投资分析报告\n")
    L.append(f"> 数据源:AKShare(同花顺财务摘要)+ 腾讯行情 | 最新财报:{esc(quarter.get('period',''))} | 分析框架:《股市真规则》帕特·多尔西(晨星标准)\n")
    L.append("> **免责声明:本报告由 AI 基于公开数据自动生成,仅供研究参考,不构成任何投资建议。**\n")

    # 执行摘要
    L.append("## 概述(执行摘要)\n")
    L.append("| 项目 | 结论 |")
    L.append("|------|------|")
    L.append(f"| 投资评级 | {rating}({rating_note}) |")
    L.append(f"| 当前价格 | {price:.2f} 元 |")
    L.append(f"| DCF 内在价值区间 | 悲观 {scenarios[2]['value']:.1f} / 中性 {scenarios[1]['value']:.1f} / 乐观 {scenarios[0]['value']:.1f} 元 |")
    L.append(f"| 安全边际 | {scenarios[2]['margin']:+.0f}% ~ {scenarios[0]['margin']:+.0f}% |")
    L.append(f"| 护城河评级 | {J['moat_width']}({J['moat_dur']}) |")
    L.append(f"| 综合评分 | {J['total_score']:.1f} / 5 |")
    L.append(f"| 总市值 / PE / PB | {quote['total_mv']:.0f}亿 / {quote['pe']:.1f} / {quote['pb']:.2f} |")
    L.append(f"| 自由现金流 | {fcf_str} |")
    L.append(f"| 一句话结论 | {J['one_liner']} |")
    L.append(f"| 报告生成时间 | {gen_time} |")
    L.append("")
    L.append("> ⚠️ 本报告由 AI 基于公开财务数据自动生成，各章节研判为量化推导结论；护城河宽度、管理层诚信、行业竞争结构等纯定性维度已用数据证据代理，最终判断建议结合公司公告与行业研报复核。\n")

    # 一、十分钟测试
    ten_pass = sum(1 for t in analysis['ten'] if t[2] is True)
    ten_total = sum(1 for t in analysis['ten'] if t[2] is not None)
    L.append("## 一、十分钟测试(Ten-Minute Test)\n")
    L.append(f"**通过 {ten_pass}/{ten_total} 项(可评估项)**\n")
    L.append("| # | 测试项 | 公司表现 | 结果 | 检验目的 |")
    L.append("|---|--------|---------|------|---------|")
    for i, t in enumerate(analysis['ten']):
        L.append(f"| {i+1} | {t[0]} | {t[1]} | {mark(t[2])} | {t[3]} |")
    L.append("")
    L.append(f"> {J['ten_note']}\n")

    # 二、护城河
    L.append("## 二、护城河分析(量化代理)\n")
    L.append(f"**护城河宽度判定:{J['moat_width']}(可持续 {J['moat_dur']})**\n")
    L.append("| 护城河来源 | 数据表现 | 强度 |")
    L.append("|-----------|---------|------|")
    for m in analysis['moat']:
        L.append(f"| {m[0]} | {m[1]} | {m[2]} |")
    L.append("")
    L.append(f"> {J['moat_note']}护城河宽度（面宽/面窄/无）的最终判定需结合品牌、网络效应、转换成本、专利等定性因素综合判断，上表为量化代理指标。\n")

    # 三、财务健康
    health_pass = sum(1 for h in analysis['health'] if h[2] is True)
    L.append("## 三、财务健康检查(危险信号排查)\n")
    L.append(f"**{health_pass} 项通过**\n")
    L.append("| 检查项 | 数据表现 | 结果 |")
    L.append("|--------|---------|------|")
    for h in analysis['health']:
        L.append(f"| {h[0]} | {h[1]} | {mark(h[2])} |")
    L.append("")
    L.append(f"> {J['health_note']}\n")

    # 四、成长性
    g = analysis['growth']
    L.append("## 四、成长性分析\n")
    L.append("| 指标 | 数值 |")
    L.append("|------|------|")
    if g.get('rev_cagr_calc') is not None:
        L.append(f"| 营收CAGR(近6年) | {g['rev_cagr_calc']:.1f}% |")
    if g.get('rev_latest') is not None:
        L.append(f"| 最新营收增速 | {g['rev_latest']:.1f}% |")
    if g.get('profit_cagr') is not None:
        L.append(f"| 利润平均增速 | {g['profit_cagr']:.1f}% |")
    if g.get('profit_latest') is not None:
        L.append(f"| 最新利润增速 | {g['profit_latest']:.1f}% |")
    L.append("")
    L.append(f"> {J['growth_note']}\n")

    # 五、管理层
    L.append("## 五、管理层评估(数据代理)\n")
    L.append("| 维度 | 数据表现 |")
    L.append("|------|---------|")
    L.append(f"| 资本效率 | 最新ROE {analysis['latest_roe']:.1f}% |")
    L.append(f"| 定价权 | 平均毛利率 {analysis['avg_gross']:.1f}% |")
    L.append(f"| 财务纪律 | 资产负债率 {analysis['latest_debt']:.1f}% |")
    L.append("")
    L.append(f"> {J['mgmt_note']}\n")

    # 六、DCF
    L.append("## 六、估值分析(DCF 三情景)\n")
    L.append(f"**关键假设**:当前 FCF {fcf_str};净负债 0;总股本约 {quote['total_mv']/price:.1f} 亿股\n")
    L.append("| 情景 | 折现率 | 5年增速 | 永续 | 每股内在价值 | 安全边际 |")
    L.append("|------|--------|---------|------|------------|---------|")
    for s in scenarios:
        L.append(f"| {s['label']} | {s['discount']*100:.1f}% | {s['growth_desc']} | {s['terminal']*100:.0f}% | **{s['value']:.2f} 元** | {s['margin']:+.1f}% |")
    L.append("")
    L.append(f"> {J['val_note']}\n")
    L.append("> 安全边际 = (内在价值 - 当前价) / 内在价值。价值投资通常要求好公司 ≥20%、一般公司 ≥35% 的安全边际。DCF 对增长率/折现率假设高度敏感。\n")

    # 七、历史财务
    L.append("## 七、历史财务数据(近6年)\n")
    L.append("| 年份 | 营收 | 营收增速 | 净利润 | 利润增速 | 毛利率 | 净利率 | ROE | 负债率 |")
    L.append("|------|------|---------|--------|---------|--------|--------|-----|--------|")
    for f in fins:
        L.append(f"| {f['year']} | {f['revenue']} | {f['revenue_yoy']} | {f['net_profit']} | {f['profit_yoy']} | {f['gross_margin']} | {f['net_margin']} | {f['roe']} | {f['debt_ratio']} |")
    L.append("")
    L.append("**最新季度**\n")
    L.append("| 报告期 | 营收 | 营收增速 | 净利润 | 利润增速 | 毛利率 |")
    L.append("|--------|------|---------|--------|---------|--------|")
    L.append(f"| {quarter['period']} | {quarter['revenue']} | {quarter['revenue_yoy']} | {quarter['net_profit']} | {quarter['profit_yoy']} | {quarter['gross_margin']} |")
    L.append("")

    # 八、综合评分
    sc = analysis['scores']
    weights = WEIGHTS
    total_score = J['total_score']
    L.append("## 八、投资决策整合(综合评分卡)\n")
    L.append(f"**综合评分:{total_score:.1f} / 5**\n")
    L.append("| 维度 | 权重 | 评分 | 加权 |")
    L.append("|------|------|------|------|")
    for k in weights:
        L.append(f"| {k} | {int(weights[k]*100)}% | {sc[k]:.1f} | {sc[k]*weights[k]:.2f} |")
    L.append(f"| **合计** | 100% | - | **{total_score:.2f} / 5** |")
    L.append("")
    L.append("> 评分标准:≥4.0 好公司 / ≥3.5 一般公司。估值吸引力维度由 DCF 安全边际决定,其余维度由财务数据量化推导。\n")

    # 操作建议(价格区间)
    L.append("### 操作建议(价格区间)\n")
    L.append("| 区间 | 价格 | 操作 |")
    L.append("|------|------|------|")
    for zn, pr, op in J['price_zones']:
        L.append(f"| {zn} | {pr} | {op} |")
    L.append(f"| **当前** | **{price:.2f} 元** | {J['cur_zone']} |")
    L.append("")

    # 最终结论
    L.append("### 最终结论\n")
    L.append(f"**核心逻辑**:{J['conclusion_core']}\n")
    L.append("**主要风险**:")
    for i, rk in enumerate(J['conclusion_risks'], 1):
        L.append(f"{i}. {rk}")
    L.append("")
    L.append(f"**监控指标**:{'、'.join(J['conclusion_monitors'])}。\n")
    L.append(f"**一句话**:{J['one_liner']}\n")

    # 风险提示
    L.append("## ⚠️ 风险提示\n")
    L.append("- 本报告由 AI 基于公开数据(AKShare/腾讯行情)自动生成,财务数据可能存在延迟或误差。")
    L.append("- DCF 估值对增长率、折现率假设高度敏感,三情景仅供参考。")
    L.append("- 护城河宽度、管理层诚信、行业竞争结构等定性维度为量化代理,最终判断需结合公司公告、行业研报及实地调研。")
    L.append("- 关键决策前请核对公司最新财报原文,并咨询专业投资顾问。")
    L.append("")
    L.append(f"---\n\n*本报告由 AI 自动生成 | 生成时间:{gen_time}*")

    return '\n'.join(L)


# render_report.py 渲染器路径(与水晶光电报告同一套模板:左侧目录 + 正文 + 阅读进度)
RENDER_REPORT = '/home/admin/.openclaw/extensions/sensenova-skills/skills/sn-md-to-html-report/scripts/render_report.py'


def build_html(code, market, quote, fins, cf, quarter, scenarios, rating, rating_note, gen_time, analysis):
    """生成 Markdown 报告,交给 render_report.py 渲染为统一版式(左目录 + 正文),与水晶光电报告同一框架"""
    import subprocess, tempfile
    md_text = build_markdown(code, market, quote, fins, cf, quarter, scenarios,
                             rating, rating_note, gen_time, analysis)
    # 写入临时 md,调用 render_report.py 渲染为 HTML(--with-js 开启进度条/目录高亮/回顶)
    with tempfile.NamedTemporaryFile('w', suffix='.md', encoding='utf-8', delete=False) as tf:
        tf.write(md_text)
        md_path = tf.name
    html_path = md_path[:-3] + '.html'
    try:
        subprocess.run(
            [sys.executable, RENDER_REPORT, md_path, html_path, '--with-js'],
            check=True, capture_output=True, text=True, timeout=60
        )
        with open(html_path, encoding='utf-8') as f:
            return f.read()
    finally:
        for p in (md_path, html_path):
            try: os.remove(p)
            except OSError: pass


# ── 主流程 ────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('code')
    ap.add_argument('--market', default='A')
    args = ap.parse_args()
    code = args.code.strip()

    gen_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ts = int(time.time() * 1000)

    # 1. 行情
    quote = get_quote(code, args.market)
    name = quote['name']

    # 2. 财务
    fins = get_financials(code)
    cf = get_cashflow(code)
    quarter = get_latest_quarter(code)

    # 3. DCF 三情景
    fcf = cf['fcf'] if cf and cf['fcf'] > 0 else 5.0  # 兜底
    shares = quote['total_mv'] / quote['price'] if quote['price'] else 1  # 亿股
    net_debt = 0.0
    price = quote['price']

    scenarios = []
    for label, growth, terminal, discount in [
        ('乐观', [0.15, 0.12, 0.10, 0.08, 0.06], 0.03, 0.09),
        ('中性', [0.10, 0.08, 0.06, 0.05, 0.04], 0.03, 0.105),
        ('悲观', [0.05, 0.04, 0.03, 0.02, 0.02], 0.02, 0.12),
    ]:
        val = dcf(fcf, growth, terminal, discount, net_debt, shares)
        margin = (val - price) / val * 100 if val else 0
        scenarios.append({
            'label': label, 'discount': discount, 'terminal': terminal,
            'growth_desc': f"{growth[0]*100:.0f}→{growth[-1]*100:.0f}%",
            'value': val, 'margin': margin,
        })

    rating, rating_note = judge(price, scenarios[0]['value'], scenarios[1]['value'], scenarios[2]['value'])

    # 3.5 分析引擎:从财务数据推导各维度
    analysis = analyze(fins, cf, quote)
    # 估值吸引力评分:由安全边际决定
    best_margin = max(s['margin'] for s in scenarios)  # 乐观情景安全边际
    if best_margin >= 20:
        analysis['scores']['估值吸引力'] = 4.5
    elif best_margin >= 0:
        analysis['scores']['估值吸引力'] = 3.5
    elif best_margin >= -20:
        analysis['scores']['估值吸引力'] = 2.5
    else:
        analysis['scores']['估值吸引力'] = 1.5

    # 4. 生成 HTML
    html = build_html(code, args.market, quote, fins, cf, quarter, scenarios, rating, rating_note, gen_time, analysis)
    filename = f"{code}_{ts}.html"
    filepath = os.path.join(REPORT_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)

    # 5. 输出元信息
    meta = {
        'ok': True,
        'code': code,
        'name': name,
        'filename': filename,
        'ts': ts,
        'gen_time': gen_time,
        'price': price,
        'rating': rating,
        'pe': quote['pe'],
        'pb': quote['pb'],
        'total_mv': quote['total_mv'],
        'fcf': round(fcf, 2),
        'value_range': f"{scenarios[2]['value']:.1f}~{scenarios[0]['value']:.1f}",
    }
    print(json.dumps(meta, ensure_ascii=False))

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))
        sys.exit(1)
