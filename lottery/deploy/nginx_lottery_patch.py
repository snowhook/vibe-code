#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 gr.czyuma.com.conf 增加 /lottery/ 反向代理（幂等，自动备份原配置）"""
import shutil
import sys
import time

P = '/etc/nginx/conf.d/gr.czyuma.com.conf'
s = open(P, encoding='utf-8').read()

if '/lottery/' in s:
    print('SKIP: already patched')
    sys.exit(0)

BLOCK = """    # 彩票选号与记账 → Python(5010)
    location = /lottery { return 301 /lottery/; }
    location ^~ /lottery/ {
        proxy_pass http://127.0.0.1:5010/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_connect_timeout 60s;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

"""

anchor = '    location @gateway {'
if anchor not in s:
    print('FAIL: anchor not found')
    sys.exit(1)

shutil.copy2(P, P + '.bak-' + time.strftime('%Y%m%d%H%M%S'))
open(P, 'w', encoding='utf-8').write(s.replace(anchor, BLOCK + anchor, 1))
print('PATCHED')
