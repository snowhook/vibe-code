# -*- coding: utf-8 -*-
"""打印各彩种走势图首行 <td> 列表，用于与库内官方数据比对列布局"""
import re
import sys
import urllib.request

sys.path.insert(0, '/home/admin/.openclaw/workspace/lottery')
import store

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
PAGE = {'ssq': 'newinc', 'dlt': 'newinc', 'sd': 'inc', 'pls': 'inc',
        'plw': 'inc', 'qxc': 'inc', 'qlc': 'newinc'}
CODELOT = {'ssq': 'ssq', 'dlt': 'dlt', 'sd': '3d', 'pls': 'pl3',
           'plw': 'pl5', 'qxc': 'qxc', 'qlc': 'qlc'}

for slug, page in PAGE.items():
    lot = CODELOT[slug]
    url = 'https://datachart.500.com/%s/history/%s/history.php?limit=3' % (slug, page)
    req = urllib.request.Request(url, headers={'User-Agent': UA,
                                               'Referer': 'https://datachart.500.com/%s/' % slug})
    try:
        html = urllib.request.urlopen(req, timeout=40).read().decode('utf-8', 'replace')
    except Exception as e:
        print('%-5s 抓取失败 %s' % (lot, e))
        continue
    m = re.search(r'<tr class="t_tr1">(.*?)</tr>', html, re.S)
    if not m:
        print('%-5s 未匹配到 t_tr1 行' % lot)
        continue
    body = re.sub(r'<!--.*?-->', '', m.group(1), flags=re.S)
    tds = [re.sub(r'<[^>]*>', '', t).replace('&nbsp;', '@').strip()
           for t in re.findall(r'<td[^>]*>(.*?)</td>', body, re.S)]
    d = store.latest_issue(lot)
    dr = store.get_draw(lot, d)
    print('%-5s (%s) 共%d列' % (lot, slug, len(tds)))
    for i, v in enumerate(tds):
        print('      [%2d] %s' % (i, v))
    if dr:
        print('      库内官方: z1=%s z2=%s sales=%s pool=%s c1=%s a1=%s'
              % (dr['zone1'], dr['zone2'], dr['sales'], dr['pool'],
                 dr['first_prize_count'], dr['first_prize_amount']))
    print()
