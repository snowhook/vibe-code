# -*- coding: utf-8 -*-
"""端到端：投注 -> 结算 -> 账本一致性（含浮动奖、多注累加、删除级联）"""
import json
import os
import ssl
import urllib.request
import http.cookiejar

BASE = 'https://127.0.0.1/lottery'
HOST = 'gr.czyuma.com'
ISSUE = '2026116'                      # 双色球，开奖 10,19,24,27,28,33 | 12
CTX = ssl._create_unverified_context()

jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar),
    urllib.request.HTTPSHandler(context=CTX))


def req(path, data=None, host=HOST):
    headers = {'Host': host, 'Content-Type': 'application/json'}
    r = urllib.request.Request(BASE + '/api/' + path,
                               data=json.dumps(data).encode() if data is not None else None,
                               headers=headers)
    return json.loads(op.open(r, timeout=180).read().decode())


ok = req('login', {'username': os.environ.get('LT_USER', 'admin'),
                   'password': os.environ.get('LT_PASS', '')})['ok']
print('1) 登录: %s' % ok)

print('2) 投注（针对已开奖的 %s，开奖号 10,19,24,27,28,33 | 12）' % ISSUE)
cases = [
    ('一等奖 6+1', {'r': [10, 19, 24, 27, 28, 33], 'b': [12]}),
    ('四等奖 4+1', {'r': [10, 19, 24, 27, 1, 2], 'b': [12]}),
    ('六等奖 0+1', {'r': [1, 2, 3, 4, 5, 6], 'b': [12]}),
    ('未中奖    ', {'r': [1, 2, 3, 4, 5, 6], 'b': [1]}),
]
for name, nums in cases:
    r = req('bet', {'lottery': 'ssq', 'play': 'std', 'numbers': nums,
                    'issue': ISSUE, 'note': name.strip()})
    print('   %s -> sets=%s 花费=%s' % (name, r['sets'], r['amount']))

ids = [t['id'] for t in req('tickets?lottery=ssq&issue=' + ISSUE)['tickets']]
print('   票据数=%d' % len(ids))

led = req('ledger?lottery=ssq')
row = [r for r in led['rows'] if r['issue'] == ISSUE][0]
print('3) 投注后账本: sets=%s cost=%s 回款=%s settled=%s'
      % (row['sets'], row['cost'], row['return_amount'], row['settled']))

print('4) 结算')
for it in req('settle', {})['settled']:
    print('   %s 回款=%.2f 命中=%s' % (it['issue'], it['ret'], it['hits'][:5]))

led = req('ledger?lottery=ssq')
row = [r for r in led['rows'] if r['issue'] == ISSUE][0]
print('5) 结算后账本: sets=%s cost=%s 回款=%s settled=%s'
      % (row['sets'], row['cost'], row['return_amount'], row['settled']))

print('6) 清理（删除全部测试票据）')
for i in ids:
    req('ticket/delete', {'id': i})
led = req('ledger?lottery=ssq')
print('   剩余票据=%d 账本行=%d 汇总=%s'
      % (len(req('tickets?lottery=ssq')['tickets']), len(led['rows']), led['summary']))
