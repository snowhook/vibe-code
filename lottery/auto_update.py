#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_update.py — 定时任务：更新全部彩种开奖数据并结算所有用户账本

由 lottery-update.timer 触发（每日 22:10 / 次日 09:10 各一次，中国时间）。
官方源优先，被拦截时自动降级到 500.com 镜像。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as appmod          # noqa: E402  复用结算逻辑（导入无副作用）
import fetcher                # noqa: E402
import store                  # noqa: E402

ORDER = ['ssq', 'dlt', '3d', 'pl3', 'pl5', 'qxc', 'qlc', 'kl8']
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'auto_update.log')


def log(msg):
    line = '[%s] %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def main():
    store.init_db()
    log('=== 自动更新开始 ===')
    total_new = 0
    for c in ORDER:
        try:
            rows, tag = fetcher.fetch(c, store.latest_issue(c), 4, source='auto')
            n = store.upsert_draws(rows, only_new=(tag == 'mirror'))
            store.log_update(c, n, note=tag)
            total_new += n
            latest = store.latest_issue(c)
            if n:
                log('%-5s +%d 期（来源 %s）-> 最新 %s' % (c, n, tag, latest))
            else:
                log('%-5s 无新期（来源 %s）-> 最新 %s' % (c, tag, latest))
        except Exception as e:
            log('%-5s 抓取失败: %s: %s' % (c, type(e).__name__, e))

    settled_n = 0
    for u in store.list_users():
        try:
            r = appmod.settle_user(u['id'])
            if r:
                settled_n += len(r)
                for it in r:
                    log('  结算 %s %s -> 回款 %s 元 %s'
                        % (u['username'], it['issue'], it['ret'],
                           ('命中 ' + '; '.join(it['hits'][:3])) if it['hits'] else ''))
        except Exception as e:
            log('  结算用户 %s 失败: %s: %s' % (u['username'], type(e).__name__, e))
    log('=== 完成：新增 %d 期，结算 %d 条 ===' % (total_new, settled_n))


if __name__ == '__main__':
    main()
