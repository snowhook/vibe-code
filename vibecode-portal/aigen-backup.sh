#!/bin/bash
# 每日备份：aigen.db 一致性快照（VACUUM INTO）+ 旧 routing.db kv 导出（回滚镜像），各留 14 天
# 2026-11-25 拆分：aigen.db 已迁至 /home/admin/apps/vibecode-aigen/（旧 workspace 副本仅留作回滚）
cd /home/admin/apps/vibecode-aigen || exit 1
python3 - <<'PY'
import aigen_store as a, sqlite3, json, os, time, glob
# 1) aigen.db 一致性快照 + 14 天保留（快照仍写回旧 backups 目录，保持历史延续）
try:
    r = a.backup(out_dir='/home/admin/.openclaw/workspace/backups/aigen')
    print("AIGEN_BACKUP", json.dumps(r, ensure_ascii=False))
except Exception as e:
    print("AIGEN_BACKUP_FAIL", repr(e))
# 2) 旧 routing.db kv 整表导出（观察期回滚镜像）+ 14 天保留
bdir = '/home/admin/.openclaw/workspace/backups/kv'
os.makedirs(bdir, exist_ok=True)
ts = time.strftime('%Y%m%d-%H%M%S')
try:
    conn = sqlite3.connect(a.ROUTING_DB)
    rows = conn.execute('SELECT key,value,updated_at FROM kv').fetchall()
    conn.close()
    p = os.path.join(bdir, 'kv-dump-%s.json' % ts)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump({k: {'value': v, 'updated_at': u} for k, v, u in rows}, f, ensure_ascii=False)
    print("KV_DUMP", p, len(rows), "keys", os.path.getsize(p), "bytes")
    cutoff = time.time() - 14 * 86400
    for old in glob.glob(os.path.join(bdir, 'kv-dump-*.json')):
        if os.path.getmtime(old) < cutoff:
            os.remove(old); print("PRUNE", old)
except Exception as e:
    print("KV_DUMP_FAIL", repr(e))
PY