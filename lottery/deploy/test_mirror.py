# -*- coding: utf-8 -*-
"""镜像源终检：号码 + 销售额 + 奖池 与库内官方数据逐项比对"""
import sys
import time
sys.path.insert(0, '/home/admin/.openclaw/workspace/lottery')
import fetcher
import store

ORDER = ['ssq', 'dlt', '3d', 'pl3', 'pl5', 'qxc', 'qlc', 'kl8']
norm = lambda s: [int(x) for x in str(s or '').split(',') if str(x).strip()]
print('%-5s %-9s %-7s %-6s %-30s %-22s %s' % (
    '彩种', '期号', '来源', '耗时', '号码(归一化)', '销售额 镜像/官方', '奖池 镜像/官方'))
print('-' * 118)
bad = []
for c in ORDER:
    t0 = time.time()
    try:
        rows, tag = fetcher.fetch(c, None, 4, source='mirror')
    except Exception as e:
        print('%-5s 失败 %s: %s' % (c, type(e).__name__, e))
        bad.append(c)
        continue
    dt = time.time() - t0
    top = rows[-1]
    d = store.get_draw(c, top[1])
    m, o = (norm(top[3]), norm(top[4])), (norm(d['zone1']), norm(d['zone2']))
    ok = 'OK' if m == o else '★号码不一致★'
    if m != o:
        bad.append((c, m, o))
    s_ok = '' if abs((top[5] or 0) - (d['sales'] or 0)) < 1 else '  ★'
    p_ok = '' if abs((top[6] or 0) - (d['pool'] or 0)) < 2 else '  ★'
    print('%-5s %-9s %-7s %-6s %-30s %-22s %s  %s' % (
        c, top[1], tag, '%.1fs' % dt,
        ('%s|%s' % (m[0], m[1]))[:30],
        '%.0f / %.0f%s' % (top[5] or 0, d['sales'] or 0, s_ok),
        '%.0f / %.0f%s' % (top[6] or 0, d['pool'] or 0, p_ok), ok))
print()
print('结论：%s' % ('8 彩种号码与官方完全一致 ✓' if not bad else '问题 -> %s' % bad))
