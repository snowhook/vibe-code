#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""import_draws.py — 导入 draws.jsonl.gz 到 SQLite"""
import gzip
import json
import sys

sys.path.insert(0, '/home/admin/.openclaw/workspace/lottery')
import store


def main(path):
    store.init_db()
    rows, n = [], 0
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            rows.append((d.get('lottery'), d.get('issue'), d.get('draw_date'),
                         d.get('zone1'), d.get('zone2'),
                         d.get('sales') or 0, d.get('pool') or 0,
                         d.get('first_prize_count') or 0, d.get('first_prize_amount') or 0,
                         d.get('prize_table') or '', 'import'))
            n += 1
            if len(rows) >= 3000:
                store.upsert_draws(rows)
                rows = []
    if rows:
        store.upsert_draws(rows)
    st = store.stats()
    print('已导入 %d 期 | 库内合计 %d 期' % (n, st['total']))
    for p in st['per']:
        print('   %-5s %6d 期  (%s ~ %s)' % (p['lottery'], p['n'], p['a'], p['b']))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '/tmp/draws.jsonl.gz')
