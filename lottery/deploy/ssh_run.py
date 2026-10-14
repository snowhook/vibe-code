#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deployment SSH helper: exec / put / get / sudo-exec over Paramiko."""
import json
import os
import sys
import paramiko

HERE = os.path.dirname(os.path.abspath(__file__))
CRED = os.path.join(HERE, 'cred.json')


def connect(user=None, password=None):
    c = json.load(open(CRED, encoding='utf-8'))
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw = dict(
        port=c.get('port', 22),
        username=user or c['user'],
        timeout=25, banner_timeout=40, auth_timeout=40,
        look_for_keys=False, allow_agent=False,
    )
    if c.get('pem'):
        kw['pkey'] = paramiko.RSAKey.from_private_key_file(c['pem'])
    else:
        kw['password'] = password or c['pass']
    cli.connect(c['host'], **kw)
    return cli


def run(cli, cmd, timeout=180, quiet=False):
    stdin, out, err = cli.exec_command(cmd, timeout=timeout, get_pty=False)
    o = out.read().decode('utf-8', 'replace')
    e = err.read().decode('utf-8', 'replace')
    rc = out.channel.recv_exit_status()
    if not quiet:
        if o:
            sys.stdout.write(o)
        if e:
            sys.stderr.write(e)
    return rc, o, e


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    action = sys.argv[1]
    cli = connect()

    if action == 'exec':
        rc, _, _ = run(cli, sys.argv[2], timeout=int(sys.argv[3]) if len(sys.argv) > 3 else 180)
        cli.close()
        return rc
    if action == 'put':
        sftp = cli.open_sftp()
        sftp.put(sys.argv[2], sys.argv[3])
        sftp.close()
        cli.close()
        print('uploaded ->', sys.argv[3])
        return 0
    if action == 'putdir':
        sftp = cli.open_sftp()
        local, remote = sys.argv[2], sys.argv[3]
        for root, dirs, files in os.walk(local):
            rel = os.path.relpath(root, local)
            rdir = remote if rel == '.' else remote + '/' + rel.replace('\\', '/')
            try:
                sftp.stat(rdir)
            except IOError:
                sftp.mkdir(rdir)
            for f in files:
                sftp.put(os.path.join(root, f), rdir + '/' + f)
                print('  up', rdir + '/' + f)
        sftp.close()
        cli.close()
        return 0
    if action == 'get':
        sftp = cli.open_sftp()
        sftp.get(sys.argv[2], sys.argv[3])
        sftp.close()
        cli.close()
        print('downloaded ->', sys.argv[3])
        return 0
    print('unknown action', action)
    return 2


if __name__ == '__main__':
    sys.exit(main())
