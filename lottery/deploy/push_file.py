#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""push_file.py — 经 SSH exec 通道以 base64 分块推送文件（SFTP 不可用时的替代）"""
import base64
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ssh_run import connect, run


def push(local, remote, chunk=9000):
    raw = open(local, 'rb').read()
    data = base64.b64encode(raw).decode()
    cli = connect()
    tmp = '/tmp/.push_%d' % int(time.time())
    run(cli, 'rm -f %s' % tmp, quiet=True)
    total = (len(data) + chunk - 1) // chunk
    for i in range(0, len(data), chunk):
        part = data[i:i + chunk]
        rc, o, e = run(cli, "printf '%%s' '%s' >> %s" % (part, tmp), quiet=True)
        if rc:
            print('chunk %d failed: %s' % (i // chunk, e))
            cli.close()
            return 1
        k = i // chunk + 1
        if k % 25 == 0 or k == total:
            print('  %d/%d chunks (%.0f%%)' % (k, total, k * 100.0 / total), flush=True)
    rc, o, e = run(cli, 'base64 -d %s > %s && rm -f %s && ls -la %s' % (tmp, remote, tmp, remote))
    cli.close()
    return rc


if __name__ == '__main__':
    sys.exit(push(sys.argv[1], sys.argv[2]))
