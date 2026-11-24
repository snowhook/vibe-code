#!/usr/bin/env python3
"""aigen_store — unified SQLite backend for the 人物生成平台 (aipose) data upgrade.

Separate database file `aigen.db` (WAL) so single-machine deployment only needs to
copy aigen.db + the image directories. Images stay on the filesystem, never in the DB.

Tables (schema v1, PRAGMA user_version=1):
  entries(key PK, kind, id, num_key, raw_json, arch, ts, name, thumb, orig, ver, updated_at)
  vault(id PK, ts, image, thumb, prompt, provider, model, studio, values_json, favorite, arch, ver, updated_at)
  records(id PK, ts, name, mode, prompt, values_json, refs_json, last_image, ver, updated_at)
  prompts(id PK, type, title, text, tags_json, favorite, ts, cat, sub, samples_json, ver, updated_at)
  docs(key PK, value_json, ver, updated_at)   -- overlay / arch / tagpool / cats / presets
  meta(key PK, value)

REST dispatch is mounted by server.py under /api/aigen/* (auth handled there).
Writes use optimistic concurrency: pass expected_ver; mismatch -> 409.
"""
import json
import os
import sqlite3
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
AIGEN_DB = os.environ.get('AIGEN_DB_PATH', os.path.join(ROOT, 'aigen.db'))
ROUTING_DB = '/home/admin/.openclaw/workspace/routing.db'  # 共享 KV（一次性 import/reconcile 用，勿改）
SCHEMA_VERSION = 4

_lock = threading.RLock()

# KV keys consumed by the one-shot importer (must match frontend src/lib/*.ts).
KV_CUSTOM = 'footprint_pose_custom_v1'
KV_ARCH = 'footprint_pose_arch_v1'
KV_OVERLAY = 'aigen_overlay_native_v1'
KV_EXPR = 'aigen_expression_v1'
KV_VAULT = 'aigen_genvault_v1'
KV_RECORDS = 'aigen_custom_records_v1'
KV_PROMPTS = 'aigen_prompts_v1'
KV_TAGPOOL = 'aigen_tagpool_v2'
KV_CATS = 'aigen_prompt_cats_v1'
KV_PRESETS = 'aigen_custom_presets_v1'

DOC_KEYS = {
    'overlay': KV_OVERLAY,
    'arch': KV_ARCH,
    'tagpool': KV_TAGPOOL,
    'cats': KV_CATS,
    'presets': KV_PRESETS,
}


def get_conn():
    conn = sqlite3.connect(AIGEN_DB, timeout=15)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=8000')
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with _lock:
        conn = get_conn()
        try:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS entries (
                    key        TEXT PRIMARY KEY,
                    kind       TEXT NOT NULL,
                    id         TEXT NOT NULL,
                    num_key    TEXT,
                    raw_json   TEXT NOT NULL,
                    arch       INTEGER NOT NULL DEFAULT 0,
                    ts         INTEGER,
                    name       TEXT,
                    thumb      TEXT,
                    orig       TEXT,
                    ver        INTEGER NOT NULL DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_entries_kind ON entries(kind);
                CREATE TABLE IF NOT EXISTS vault (
                    id          TEXT PRIMARY KEY,
                    ts          INTEGER,
                    image       TEXT,
                    thumb       TEXT,
                    prompt      TEXT,
                    provider    TEXT,
                    model       TEXT,
                    studio      TEXT,
                    values_json TEXT,
                    focus_json  TEXT,
                    favorite    INTEGER,
                    arch        INTEGER,
                    ver         INTEGER NOT NULL DEFAULT 1,
                    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS records (
                    id          TEXT PRIMARY KEY,
                    ts          INTEGER,
                    name        TEXT,
                    mode        TEXT,
                    prompt      TEXT,
                    values_json TEXT,
                    refs_json   TEXT,
                    last_image  TEXT,
                    ver         INTEGER NOT NULL DEFAULT 1,
                    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS prompts (
                    id          TEXT PRIMARY KEY,
                    type        TEXT,
                    title       TEXT,
                    text        TEXT,
                    tags_json   TEXT,
                    favorite    INTEGER,
                    ts          INTEGER,
                    cat         TEXT,
                    sub         TEXT,
                    samples_json TEXT,
                    ver         INTEGER NOT NULL DEFAULT 1,
                    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS characters (
                    id             TEXT PRIMARY KEY,
                    ts             INTEGER,
                    name           TEXT,
                    descr          TEXT,
                    identity       TEXT,
                    identity_thumb TEXT,
                    cover          TEXT,
                    cover_thumb    TEXT,
                    ver            INTEGER NOT NULL DEFAULT 1,
                    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id         TEXT PRIMARY KEY,
                    ts         INTEGER,
                    name       TEXT,
                    descr      TEXT,
                    cover      TEXT,
                    cover_thumb TEXT,
                    ver        INTEGER NOT NULL DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS asset_images (
                    id           TEXT PRIMARY KEY,
                    character_id TEXT,
                    category     TEXT,
                    label        TEXT,
                    url          TEXT,
                    thumb        TEXT,
                    prompt       TEXT,
                    provider     TEXT,
                    model        TEXT,
                    ts           INTEGER,
                    ver          INTEGER NOT NULL DEFAULT 1,
                    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_asset_char ON asset_images(character_id);
                CREATE TABLE IF NOT EXISTS docs (
                    key        TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    ver        INTEGER NOT NULL DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
            ''')
            conn.execute('INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)',
                         ('schema_version', str(SCHEMA_VERSION)))
            conn.execute('INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)',
                         ('read_source', 'kv'))
            # 幂等列迁移：旧库 vault 无 focus_json 时补列（不丢数据）
            vcols = [r[1] for r in conn.execute('PRAGMA table_info(vault)')]
            if 'focus_json' not in vcols:
                conn.execute('ALTER TABLE vault ADD COLUMN focus_json TEXT')
            # 幂等迁移：characters 增加 project_id；无项目的旧角色归入「默认项目」
            ccols = [r[1] for r in conn.execute('PRAGMA table_info(characters)')]
            if 'project_id' not in ccols:
                conn.execute('ALTER TABLE characters ADD COLUMN project_id TEXT')
            if conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0] == 0:
                conn.execute('INSERT INTO projects(id,ts,name,descr) VALUES(?,?,?,?)',
                             ('proj-default', _now_ms(), '默认项目', '自动创建：容纳升级前的既有角色'))
            conn.execute("UPDATE characters SET project_id='proj-default' WHERE project_id IS NULL OR project_id=''")
            conn.execute('INSERT INTO meta(key,value) VALUES(?,?) '
                         'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                         ('schema_version', str(SCHEMA_VERSION)))
            conn.commit()
        finally:
            conn.close()


def _now_ms():
    return int(time.time() * 1000)


def _jdump(v):
    return json.dumps(v, ensure_ascii=False)


def _jload(s, default=None):
    if s is None:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


# ── entries ──────────────────────────────────────────────────────────────────

def _entry_row(key, kind, eid, num_key, raw, arch):
    return {
        'key': key, 'kind': kind, 'id': eid, 'num_key': num_key,
        'raw_json': _jdump(raw), 'arch': 1 if arch else 0,
        'ts': raw.get('ts'), 'name': raw.get('name'),
        'thumb': raw.get('thumb'), 'orig': raw.get('orig'),
    }


def _upsert_entry(conn, r, expected_ver=None):
    cur = conn.execute('SELECT ver FROM entries WHERE key=?', (r['key'],))
    row = cur.fetchone()
    if row is not None:
        if expected_ver is not None and int(expected_ver) != row['ver']:
            return ('conflict', row['ver'])
        ver = row['ver'] + 1
        conn.execute('''UPDATE entries SET kind=?,id=?,num_key=?,raw_json=?,arch=?,ts=?,name=?,thumb=?,orig=?,
                        ver=?,updated_at=CURRENT_TIMESTAMP WHERE key=?''',
                     (r['kind'], r['id'], r['num_key'], r['raw_json'], r['arch'], r['ts'],
                      r['name'], r['thumb'], r['orig'], ver, r['key']))
    else:
        ver = 1
        conn.execute('''INSERT INTO entries(key,kind,id,num_key,raw_json,arch,ts,name,thumb,orig,ver)
                        VALUES(?,?,?,?,?,?,?,?,?,?,1)''',
                     (r['key'], r['kind'], r['id'], r['num_key'], r['raw_json'], r['arch'],
                      r['ts'], r['name'], r['thumb'], r['orig']))
    return ('ok', ver)


def entries_list(kind=None):
    conn = get_conn()
    try:
        if kind:
            rows = conn.execute('SELECT * FROM entries WHERE kind=? ORDER BY kind,id', (kind,)).fetchall()
        else:
            rows = conn.execute('SELECT * FROM entries ORDER BY kind,id').fetchall()
        out = []
        for r in rows:
            out.append({'key': r['key'], 'kind': r['kind'], 'id': r['id'], 'num_key': r['num_key'],
                        'raw': _jload(r['raw_json'], {}), 'arch': r['arch'], 'ts': r['ts'],
                        'name': r['name'], 'thumb': r['thumb'], 'orig': r['orig'], 'ver': r['ver']})
        return {'ok': True, 'items': out, 'count': len(out)}
    finally:
        conn.close()


def entries_upsert(rows):
    """rows: [{key?,kind,id,num_key?,raw,arch?,expected_ver?}]. Returns per-row results."""
    results = []
    with _lock:
        conn = get_conn()
        try:
            for it in rows:
                kind = it.get('kind')
                eid = str(it.get('id') or '')
                raw = it.get('raw') or {}
                key = it.get('key') or f'{kind}:{eid}'
                arch = it.get('arch', raw.get('arch'))
                r = _entry_row(key, kind, eid, it.get('num_key'), raw, arch)
                status, ver = _upsert_entry(conn, r, it.get('expected_ver'))
                results.append({'key': key, 'status': status, 'ver': ver})
            conn.commit()
        finally:
            conn.close()
    conflict = any(x['status'] == 'conflict' for x in results)
    return (409 if conflict else 200), {'ok': not conflict, 'results': results}


def entry_patch(key, patch):
    """patch: {raw?, arch?, expected_ver?}. Merges raw if provided."""
    with _lock:
        conn = get_conn()
        try:
            row = conn.execute('SELECT * FROM entries WHERE key=?', (key,)).fetchone()
            if row is None:
                return 404, {'ok': False, 'error': 'not found'}
            if patch.get('expected_ver') is not None and int(patch['expected_ver']) != row['ver']:
                return 409, {'ok': False, 'error': 'version conflict', 'ver': row['ver']}
            raw = _jload(row['raw_json'], {})
            if 'raw' in patch:
                raw = patch['raw']
            arch = patch.get('arch', row['arch'])
            r = _entry_row(key, row['kind'], row['id'], row['num_key'], raw, arch)
            status, ver = _upsert_entry(conn, r)
            conn.commit()
            return 200, {'ok': True, 'key': key, 'ver': ver}
        finally:
            conn.close()


def entry_delete(key):
    with _lock:
        conn = get_conn()
        try:
            conn.execute('DELETE FROM entries WHERE key=?', (key,))
            conn.commit()
            return 200, {'ok': True}
        finally:
            conn.close()


# ── generic single-row collections: vault / records / prompts ────────────────

def _row_to_dict(r):
    return {k: r[k] for k in r.keys()}


def _list_table(table, limit, offset, order='ts DESC', where=None, where_params=()):
    conn = get_conn()
    try:
        wsql = f' WHERE {where}' if where else ''
        total = conn.execute(f'SELECT COUNT(*) c FROM {table}{wsql}', where_params).fetchone()['c']
        rows = conn.execute(f'SELECT * FROM {table}{wsql} ORDER BY {order} LIMIT ? OFFSET ?',
                            (*where_params, limit, offset)).fetchall()
        items = []
        for r in rows:
            d = _row_to_dict(r)
            for jk in ('values_json', 'refs_json', 'tags_json', 'samples_json', 'focus_json'):
                if jk in d:
                    base = jk[:-5]
                    d[base] = _jload(d.pop(jk), None)
            items.append(d)
        return {'ok': True, 'items': items, 'total': total, 'limit': limit, 'offset': offset}
    finally:
        conn.close()


def _upsert_row_conn(conn, table, pk, obj, expected_ver=None):
    """SQL helper using a caller-owned connection (no lock/commit). For batch import."""
    cols = {
        'vault': ['ts', 'image', 'thumb', 'prompt', 'provider', 'model', 'studio', 'favorite', 'arch'],
        'records': ['ts', 'name', 'mode', 'prompt', 'last_image'],
        'prompts': ['type', 'title', 'text', 'favorite', 'ts', 'cat', 'sub'],
        'characters': ['ts', 'name', 'descr', 'identity', 'identity_thumb', 'cover', 'cover_thumb', 'project_id'],
        'asset_images': ['character_id', 'category', 'label', 'url', 'thumb', 'prompt', 'provider', 'model', 'ts'],
        'projects': ['ts', 'name', 'descr', 'cover', 'cover_thumb'],
    }[table]
    jcols = {
        'vault': {'values': 'values_json', 'focus': 'focus_json'},
        'records': {'values': 'values_json', 'refs': 'refs_json'},
        'prompts': {'tags': 'tags_json', 'samples': 'samples_json'},
        'characters': {},
        'asset_images': {},
        'projects': {},
    }[table]
    row = conn.execute(f'SELECT ver FROM {table} WHERE {pk}=?', (obj.get(pk),)).fetchone()
    vals = {c: obj.get(c) for c in cols}
    for src, dst in jcols.items():
        vals[dst] = _jdump(obj.get(src)) if obj.get(src) is not None else None
    if row is not None:
        if expected_ver is not None and int(expected_ver) != row['ver']:
            return 409, {'ok': False, 'error': 'version conflict', 'ver': row['ver']}
        ver = row['ver'] + 1
        sets = ','.join(f'{c}=?' for c in vals)
        conn.execute(f'UPDATE {table} SET {sets},ver=?,updated_at=CURRENT_TIMESTAMP WHERE {pk}=?',
                     (*vals.values(), ver, obj.get(pk)))
    else:
        ver = 1
        keys = [pk, *vals.keys(), 'ver']
        ph = ','.join('?' for _ in keys)
        conn.execute(f'INSERT INTO {table}({",".join(keys)}) VALUES({ph})',
                     (obj.get(pk), *vals.values(), 1))
    return 200, {'ok': True, pk: obj.get(pk), 'ver': ver}


def _upsert_row(table, pk, obj, expected_ver=None):
    with _lock:
        conn = get_conn()
        try:
            res = _upsert_row_conn(conn, table, pk, obj, expected_ver)
            conn.commit()
            return res
        finally:
            conn.close()


def _patch_row(table, pk, val, body):
    """PATCH：把 body 合并到现有行（保留未指定列）再 upsert，避免部分更新清空其它列。"""
    with _lock:
        conn = get_conn()
        try:
            r = conn.execute(f'SELECT * FROM {table} WHERE {pk}=?', (val,)).fetchone()
            if r is None:
                return 404, {'ok': False, 'error': 'not found'}
            existing = _row_to_dict(r)
            for jk in ('values_json', 'refs_json', 'tags_json', 'samples_json', 'focus_json'):
                if jk in existing:
                    existing[jk[:-5]] = _jload(existing.pop(jk), None)
            merged = {**existing, **body, pk: val}
            res = _upsert_row_conn(conn, table, pk, merged, body.get('expected_ver'))
            conn.commit()
            return res
        finally:
            conn.close()


def _delete_row(table, pk, val):
    with _lock:
        conn = get_conn()
        try:
            conn.execute(f'DELETE FROM {table} WHERE {pk}=?', (val,))
            conn.commit()
            return 200, {'ok': True}
        finally:
            conn.close()


# ── docs (CAS whole-document) ────────────────────────────────────────────────

def doc_get(key):
    conn = get_conn()
    try:
        row = conn.execute('SELECT * FROM docs WHERE key=?', (key,)).fetchone()
        if row is None:
            return {'ok': True, 'key': key, 'value': None, 'ver': 0}
        return {'ok': True, 'key': key, 'value': _jload(row['value_json'], None), 'ver': row['ver']}
    finally:
        conn.close()


def doc_put(key, value, expected_ver=None):
    with _lock:
        conn = get_conn()
        try:
            row = conn.execute('SELECT ver FROM docs WHERE key=?', (key,)).fetchone()
            if row is not None:
                if expected_ver is not None and int(expected_ver) != row['ver']:
                    return 409, {'ok': False, 'error': 'version conflict', 'ver': row['ver']}
                ver = row['ver'] + 1
                conn.execute('UPDATE docs SET value_json=?,ver=?,updated_at=CURRENT_TIMESTAMP WHERE key=?',
                             (_jdump(value), ver, key))
            else:
                ver = 1
                conn.execute('INSERT INTO docs(key,value_json,ver) VALUES(?,?,1)', (key, _jdump(value)))
            conn.commit()
            return 200, {'ok': True, 'key': key, 'ver': ver}
        finally:
            conn.close()


# ── meta / counts ────────────────────────────────────────────────────────────

def meta_get():
    conn = get_conn()
    try:
        m = {r['key']: r['value'] for r in conn.execute('SELECT key,value FROM meta').fetchall()}
        counts = {}
        for t in ('entries', 'vault', 'records', 'prompts', 'docs'):
            counts[t] = conn.execute(f'SELECT COUNT(*) c FROM {t}').fetchone()['c']
        ek = {r['kind']: r['c'] for r in
              conn.execute('SELECT kind,COUNT(*) c FROM entries GROUP BY kind').fetchall()}
        return {'ok': True, 'schema_version': int(m.get('schema_version', SCHEMA_VERSION)),
                'read_source': m.get('read_source', 'kv'), 'counts': counts, 'entries_by_kind': ek}
    finally:
        conn.close()


def set_read_source(src):
    with _lock:
        conn = get_conn()
        try:
            conn.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=?',
                         ('read_source', src, src))
            conn.commit()
            return 200, {'ok': True, 'read_source': src}
        finally:
            conn.close()


# ── one-shot import from routing.db KV ───────────────────────────────────────

def _kv_value(conn, key):
    row = conn.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
    if row is None:
        return None
    return _jload(row['value'], None)


def import_kv():
    """Idempotent: copy KV blobs into aigen.db tables/docs. Returns counts."""
    rc = sqlite3.connect(ROUTING_DB, timeout=15)
    rc.row_factory = sqlite3.Row
    counts = {'entries': 0, 'vault': 0, 'records': 0, 'prompts': 0, 'docs': 0}
    try:
        custom = _kv_value(rc, KV_CUSTOM) or {}
        arch = _kv_value(rc, KV_ARCH) or {}
        expr = (_kv_value(rc, KV_EXPR) or {}).get('items') or {}
        vault = (_kv_value(rc, KV_VAULT) or {}).get('items') or []
        records = (_kv_value(rc, KV_RECORDS) or {}).get('items') or {}
        prompts = (_kv_value(rc, KV_PROMPTS) or {}).get('items') or {}
    finally:
        rc.close()

    with _lock:
        conn = get_conn()
        try:
            # entries: three custom kinds + expression
            for kind in ('portrait', 'costume', 'pose'):
                bag = custom.get(kind) or {}
                if isinstance(bag, list):
                    bag = {str(i): e for i, e in enumerate(bag)}
                for nk, e in bag.items():
                    if not e or not e.get('id'):
                        continue
                    eid = str(e['id'])
                    a = e.get('arch') or (arch.get(kind, {}) or {}).get(eid)
                    r = _entry_row(f'{kind}:{eid}', kind, eid, str(nk), e, a)
                    _upsert_entry(conn, r)
                    counts['entries'] += 1
            for eid, e in expr.items():
                if not e or not e.get('id'):
                    continue
                e = {**e, 'kind': 'expression'}
                r = _entry_row(f"expression:{e['id']}", 'expression', str(e['id']), None, e, e.get('arch'))
                _upsert_entry(conn, r)
                counts['entries'] += 1
            # vault
            for it in vault:
                if not it.get('id'):
                    continue
                _upsert_row_conn(conn, 'vault', 'id', it)
                counts['vault'] += 1
            # records
            for rid, it in (records.items() if isinstance(records, dict) else [(x.get('id'), x) for x in records]):
                if not it or not it.get('id'):
                    continue
                _upsert_row_conn(conn, 'records', 'id', it)
                counts['records'] += 1
            # prompts
            for pid, it in (prompts.items() if isinstance(prompts, dict) else [(x.get('id'), x) for x in prompts]):
                if not it or not it.get('id'):
                    continue
                _upsert_row_conn(conn, 'prompts', 'id', it)
                counts['prompts'] += 1
            # docs
            for dk, kvk in DOC_KEYS.items():
                rc2 = sqlite3.connect(ROUTING_DB, timeout=15)
                rc2.row_factory = sqlite3.Row
                try:
                    val = _kv_value(rc2, kvk)
                finally:
                    rc2.close()
                if val is not None:
                    conn.execute('INSERT INTO docs(key,value_json,ver) VALUES(?,?,1) '
                                 'ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
                                 (dk, _jdump(val)))
                    counts['docs'] += 1
            conn.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=?',
                         ('imported_at', str(_now_ms()), str(_now_ms())))
            conn.commit()
        finally:
            conn.close()
    return {'ok': True, 'counts': counts}


def reconcile():
    """Compare KV blob entity counts vs aigen.db table counts."""
    rc = sqlite3.connect(ROUTING_DB, timeout=15)
    rc.row_factory = sqlite3.Row
    try:
        custom = _kv_value(rc, KV_CUSTOM) or {}
        expr = (_kv_value(rc, KV_EXPR) or {}).get('items') or {}
        vault = (_kv_value(rc, KV_VAULT) or {}).get('items') or []
        records = (_kv_value(rc, KV_RECORDS) or {}).get('items') or {}
        prompts = (_kv_value(rc, KV_PROMPTS) or {}).get('items') or {}
    finally:
        rc.close()
    kv_entries = sum(len(custom.get(k) or {}) for k in ('portrait', 'costume', 'pose')) + len(expr)
    kv_counts = {'entries': kv_entries, 'vault': len(vault),
                 'records': len(records), 'prompts': len(prompts)}
    db_counts = meta_get()['counts']
    diff = {k: {'kv': kv_counts[k], 'db': db_counts.get(k, 0),
                'match': kv_counts[k] == db_counts.get(k, 0)} for k in kv_counts}
    return {'ok': True, 'diff': diff, 'all_match': all(d['match'] for d in diff.values())}


# ── backup ───────────────────────────────────────────────────────────────────

def backup(out_dir=None):
    out_dir = out_dir or os.path.join(ROOT, 'backups', 'aigen')
    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime('%Y%m%d-%H%M%S')
    dest = os.path.join(out_dir, f'aigen-{ts}.db')
    conn = get_conn()
    try:
        conn.execute('VACUUM INTO ?', (dest,))
    finally:
        conn.close()
    # retain 14 days
    cutoff = time.time() - 14 * 86400
    removed = 0
    for fn in os.listdir(out_dir):
        fp = os.path.join(out_dir, fn)
        if fn.startswith('aigen-') and fn.endswith('.db') and os.path.getmtime(fp) < cutoff:
            os.remove(fp)
            removed += 1
    return {'ok': True, 'path': dest, 'bytes': os.path.getsize(dest), 'removed_old': removed}


# ── thumbnails (Pillow) ──────────────────────────────────────────────────────

IMG_DIR = os.environ.get('FOOTPRINT_IMG_DIR', '/usr/share/nginx/html/footprint-images')
POSE_OUT_DIR = '/usr/share/nginx/html/pose-out'
# 800px / q88：网格卡片约 290px CSS， retina 2x 需 ~580px；400px/q80 实测偏软（267x400 仅 5.8KB），
# 提到 800px/q88（约 533x800 / 30-50KB）保证清晰又远小于原图。
THUMB_PX = 800
THUMB_Q = 88


def make_thumb(abs_path, url_path, force=False):
    """Generate an 800px webp thumbnail beside abs_path; return its URL path or None.
    force=True regenerates even if a thumbnail already exists (quality upgrade).
    Never raises (thumbnail failure must not break uploads)."""
    try:
        from PIL import Image
        stem, _ = os.path.splitext(abs_path)
        # 后缀编码规格：升级质量时改后缀→URL 变化→自动绕过浏览器对旧缩略图的长缓存
        thumb_abs = stem + '.t800.webp'
        if os.path.exists(thumb_abs) and not force:
            return os.path.splitext(url_path)[0] + '.t800.webp'
        with Image.open(abs_path) as im:
            im = im.convert('RGB')
            im.thumbnail((THUMB_PX, THUMB_PX))
            im.save(thumb_abs, 'WEBP', quality=THUMB_Q, method=4)
        return os.path.splitext(url_path)[0] + '.t800.webp'
    except Exception:
        return None


def _norm_url(u):
    """Normalize stored image paths to site-root absolute (mirror frontend normPath)."""
    u = str(u or '')
    while u.startswith('../'):
        u = u[3:]
    if u and not u.startswith('/') and not u.startswith('http') and not u.startswith('data:'):
        u = '/' + u
    return u


def _url_to_abs(url):
    url = _norm_url(url)
    if url.startswith('/footprint-images/'):
        return os.path.join(IMG_DIR, url[len('/footprint-images/'):])
    if url.startswith('/pose-out/'):
        return os.path.join(POSE_OUT_DIR, url[len('/pose-out/'):])
    return None


def backfill_thumbs(force=False):
    """Generate thumbnails for entries.orig and vault.image; update thumb columns.
    force=True regenerates ALL local-image thumbnails (quality upgrade), overwriting existing."""
    made = 0
    skipped = 0
    with _lock:
        conn = get_conn()
        try:
            rows = conn.execute('SELECT key,orig,thumb FROM entries').fetchall()
            for r in rows:
                orig = _norm_url(r['orig'])
                if not orig or not orig.startswith('/'):
                    skipped += 1
                    continue
                cur_thumb = _norm_url(r['thumb'])
                if not force and cur_thumb and cur_thumb != orig and os.path.exists(_url_to_abs(cur_thumb) or ''):
                    skipped += 1
                    continue
                abs_p = _url_to_abs(orig)
                if not abs_p or not os.path.exists(abs_p):
                    skipped += 1
                    continue
                t = make_thumb(abs_p, orig, force=force)
                if t:
                    conn.execute('UPDATE entries SET thumb=? WHERE key=?', (t, r['key']))
                    made += 1
            vrows = conn.execute('SELECT id,image,thumb FROM vault').fetchall()
            for r in vrows:
                img = _norm_url(r['image'])
                if not img or not img.startswith('/'):
                    skipped += 1
                    continue
                if not force and r['thumb'] and os.path.exists(_url_to_abs(r['thumb']) or ''):
                    skipped += 1
                    continue
                abs_p = _url_to_abs(img)
                if not abs_p or not os.path.exists(abs_p):
                    skipped += 1
                    continue
                t = make_thumb(abs_p, img, force=force)
                if t:
                    conn.execute('UPDATE vault SET thumb=? WHERE id=?', (t, r['id']))
                    made += 1
            conn.commit()
        finally:
            conn.close()
    return {'ok': True, 'made': made, 'skipped': skipped}


# ── REST dispatch ────────────────────────────────────────────────────────────

def dispatch(method, subpath, query, body):
    """subpath: portion after '/api/aigen', e.g. '/entries' or '/entries/pose:my-x'.
    query: dict of first values. body: parsed JSON dict (or {}). Returns (status, payload)."""
    body = body or {}
    q = query or {}
    parts = [p for p in subpath.split('/') if p]
    try:
        # meta
        if parts == ['meta'] and method == 'GET':
            return 200, meta_get()
        if parts == ['read-source'] and method == 'POST':
            return set_read_source(str(body.get('source') or 'kv'))
        # import / reconcile / backup / backfill (admin)
        if parts == ['import-kv'] and method == 'POST':
            return 200, import_kv()
        if parts == ['reconcile'] and method == 'GET':
            return 200, reconcile()
        if parts == ['backup'] and method == 'POST':
            return 200, backup(body.get('out_dir'))
        if parts == ['backfill-thumbs'] and method == 'POST':
            return 200, backfill_thumbs(force=bool(body.get('force')))
        # entries
        if parts and parts[0] == 'entries':
            if len(parts) == 1:
                if method == 'GET':
                    return 200, entries_list(q.get('kind'))
                if method == 'POST':
                    return entries_upsert(body.get('rows') or [])
            if len(parts) == 2 and parts[1] == 'upsert' and method == 'POST':
                return entries_upsert(body.get('rows') or [])
            if len(parts) == 2:
                key = parts[1]
                if method == 'GET':
                    conn = get_conn()
                    try:
                        r = conn.execute('SELECT * FROM entries WHERE key=?', (key,)).fetchone()
                    finally:
                        conn.close()
                    if r is None:
                        return 404, {'ok': False, 'error': 'not found'}
                    return 200, {'ok': True, 'key': key, 'raw': _jload(r['raw_json'], {}),
                                 'arch': r['arch'], 'ver': r['ver']}
                if method == 'PATCH':
                    return entry_patch(key, body)
                if method == 'DELETE':
                    return entry_delete(key)
        # vault / records / prompts / characters / asset_images / projects
        if parts and parts[0] in ('vault', 'records', 'prompts', 'characters', 'asset_images', 'projects'):
            table = parts[0]
            pk = 'id'
            if len(parts) == 1:
                if method == 'GET':
                    limit = max(1, min(int(q.get('limit', 100)), 1000))
                    offset = max(0, int(q.get('offset', 0)))
                    if table == 'asset_images' and q.get('character_id'):
                        return 200, _list_table(table, limit, offset,
                                                where='character_id=?', where_params=(q['character_id'],))
                    if table == 'characters' and q.get('project_id'):
                        return 200, _list_table(table, limit, offset,
                                                where='project_id=?', where_params=(q['project_id'],))
                    return 200, _list_table(table, limit, offset)
                if method == 'POST':
                    return _upsert_row(table, pk, body, body.get('expected_ver'))
            if len(parts) == 2:
                val = parts[1]
                if method == 'PATCH':
                    return _patch_row(table, pk, val, body)
                if method == 'DELETE':
                    return _delete_row(table, pk, val)
        # docs
        if parts and parts[0] == 'docs' and len(parts) == 2:
            key = parts[1]
            if method == 'GET':
                return 200, doc_get(key)
            if method == 'PUT':
                return doc_put(key, body.get('value'), body.get('expected_ver'))
        return 404, {'ok': False, 'error': f'no route: {method} {subpath}'}
    except Exception as e:
        return 500, {'ok': False, 'error': str(e)}