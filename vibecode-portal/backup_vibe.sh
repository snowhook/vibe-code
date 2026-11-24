#!/bin/bash
# VIBE CODE 门户全量备份（2026-09-24 建）
# 内容：已部署页面 + workspace 源（含 routing.db 一致性快照）+ nginx 配置 + 生成档案
# 上传 OSS snowhook-openclaw/backups/vibe/ 后删除本地包，节省磁盘
# 每周执行一次；凭据读 .secrets/ALIYUN_OSS_AK_ID|SECRET；日志 backup.log
set -u

SECRETS="$HOME/.openclaw/workspace/.secrets"
WORKSPACE="$HOME/.openclaw/workspace"
STAGING="/tmp/vibe-backup"
STAMP=$(date +%Y%m%d-%H%M%S)
ZIP_NAME="vibe-code-backup-$STAMP.zip"
ZIP_PATH="/tmp/$ZIP_NAME"
LOG="$WORKSPACE/backups/vibe-backup.log"

OSS_BUCKET="snowhook-openclaw"
OSS_PREFIX="backups/vibe/"
OSS_ENDPOINT="oss-ap-southeast-1-internal.aliyuncs.com"
OSS_REGION="ap-southeast-1"
OSS_KEEP=8

mkdir -p "$(dirname "$LOG")"
log(){ echo "[$(date '+%F %T')] $*" >> "$LOG"; }

# ── 1. 组装 staging 目录 ──────────────────────────────────────────────────────
log "START staging"
rm -rf "$STAGING"
mkdir -p "$STAGING"/{deployed-html,workspace-src,nginx-conf,meta,systemd-units}

# 已部署页面（含 stock-reports、math-exports、math-videos、世界时钟图、足迹行程照片）
rsync -a --exclude='.git' \
  /usr/share/nginx/html/*.html \
  /usr/share/nginx/html/stock-reports \
  /usr/share/nginx/html/math-exports \
  /usr/share/nginx/html/math-videos \
  /usr/share/nginx/html/world-time-zone-map.png \
  /usr/share/nginx/html/footprint-images \
  "$STAGING/deployed-html/" 2>/dev/null

# 足迹行程照片（2026-11-10 加入：server.py 的 /api/footprint/upload 落盘于此，
# 不在 routing.db 里，只有这里备到才算真的备到）
IMG_N=$(find "$STAGING/deployed-html/footprint-images" -type f 2>/dev/null | wc -l)
IMG_SZ=$(du -sh "$STAGING/deployed-html/footprint-images" 2>/dev/null | cut -f1)
[ -n "$IMG_SZ" ] || IMG_SZ="0"
log "OK footprint-images $IMG_N files ($IMG_SZ)"

# workspace 源（排除密钥、缓存、备份目录本身、__pycache__）
rsync -a \
  --exclude='.secrets' \
  --exclude='.redfox_key' \
  --exclude='*.key' \
  --exclude='*.pem' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='backups' \
  --exclude='.git' \
  --exclude='memory' \
  "$WORKSPACE/" \
  "$STAGING/workspace-src/"

# vibecode-* 独立应用（2026-11-25 模块化拆分，每个模块独立进程+独立数据）
# 排除：math 数据（backup_math.sh 每日已单独备，避免双份 80M+）与缓存产物
 mkdir -p "$STAGING/apps-src"
rsync -a --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='vibecode-math/data' \
  /home/admin/apps/ \
  "$STAGING/apps-src/" 2>/dev/null
log "OK apps-src $(du -sh "$STAGING/apps-src" 2>/dev/null | cut -f1)"

# routing.db 一致性快照（用 sqlite3 .backup 保证事务安全）
sqlite3 "$WORKSPACE/routing.db" ".backup '$STAGING/workspace-src/routing.db'" \
  && log "OK routing.db snapshot" \
  || log "WARN routing.db snapshot failed, using raw copy"
[ -f "$STAGING/workspace-src/routing.db" ] || \
  cp -a "$WORKSPACE/routing.db" "$STAGING/workspace-src/routing.db" 2>/dev/null

# nginx 配置 + systemd 单元（含拆分出的 vibecode-* 独立服务）
cp -a /etc/nginx/default.d/*.conf   "$STAGING/nginx-conf/" 2>/dev/null
cp -a /etc/nginx/conf.d/gr.czyuma.com.conf "$STAGING/nginx-conf/" 2>/dev/null
cp -a /etc/nginx/nginx.conf         "$STAGING/nginx-conf/" 2>/dev/null
cp -a /etc/systemd/system/vibecode-*.service "$STAGING/systemd-units/" 2>/dev/null
cp -a /etc/systemd/system/routing-server.service "$STAGING/systemd-units/" 2>/dev/null

# meta 信息
date '+%F %T %Z' > "$STAGING/meta/backup-time.txt"
cat > "$STAGING/meta/README.txt" << 'META'
VIBE CODE 门户备份包目录说明
─────────────────────────────
deployed-html/  已部署到 nginx 的页面（含 stock-reports、math-exports、math-videos）
                含 footprint-images/：足迹地图的行程照片（用户上传，不在 routing.db 里）
workspace-src/  workspace 源代码与数据（routing.db 为一致性快照）
                不含：.secrets/、*.key、*.pem、__pycache__、backups/、memory/
apps-src/       /home/admin/apps 全部 vibecode-* 独立应用源码与轻量数据
                （vibecode-bazi/data、vibecode-gzh/data、vibecode-collab 缓存；
                 不含 vibecode-math/data——由 backup_math.sh 每日备份）
nginx-conf/     nginx 配置文件（default.d/*.conf、gr.czyuma.com.conf、nginx.conf）
systemd-units/  routing-server.service + vibecode-*.service 单元文件
meta/           备份时间与本说明

还原步骤：
1. 解压到目标服务器
2. deployed-html/ → /usr/share/nginx/html/（footprint-images/ 整目录一起还原，
   权限建议 chown admin:admin -R、chmod 755；页面里的照片 URL 是绝对路径 /footprint-images/…）
3. workspace-src/ → /home/admin/.openclaw/workspace/
4. apps-src/ → /home/admin/apps/（chown -R admin:admin）
5. nginx-conf/*.conf → 对应 /etc/nginx/ 路径
6. systemd-units/*.service → /etc/systemd/system/，然后
   systemctl daemon-reload && systemctl enable --now routing-server vibecode-bazi vibecode-math vibecode-gzh vibecode-collab vibecode-lockup vibecode-sentiment
7. 重新配置 .secrets/（ALI_TTS_API_KEY、ALIYUN_OSS_AK_ID/SECRET 等）
8. nginx -t && systemctl reload nginx；systemctl restart routing-server
META

FILE_COUNT=$(find "$STAGING" -type f | wc -l)
log "OK staging $FILE_COUNT files"

# ── 2. 打包 ───────────────────────────────────────────────────────────────────
cd /tmp
zip -rq "$ZIP_NAME" vibe-backup
RC=$?
if [ $RC -ne 0 ]; then
  log "FAILED zip rc=$RC"
  rm -rf "$STAGING" "$ZIP_PATH"
  exit $RC
fi
SIZE=$(du -h "$ZIP_PATH" | cut -f1)
MD5=$(md5sum "$ZIP_PATH" | cut -d' ' -f1)
log "OK zip $ZIP_PATH ($SIZE) md5=$MD5"
rm -rf "$STAGING"

# ── 3. 上传 OSS ───────────────────────────────────────────────────────────────
if [ -r "$SECRETS/ALIYUN_OSS_AK_ID" ] && [ -r "$SECRETS/ALIYUN_OSS_AK_SECRET" ]; then
  export ALIBABA_CLOUD_ACCESS_KEY_ID="$(cat "$SECRETS/ALIYUN_OSS_AK_ID")"
  export ALIBABA_CLOUD_ACCESS_KEY_SECRET="$(cat "$SECRETS/ALIYUN_OSS_AK_SECRET")"

  UP=$(aliyun oss cp "$ZIP_PATH" "oss://$OSS_BUCKET/$OSS_PREFIX" \
        -e "$OSS_ENDPOINT" --region "$OSS_REGION" -f 2>&1)
  UP_RC=$?

  if [ $UP_RC -eq 0 ]; then
    log "OK oss://$OSS_BUCKET/$OSS_PREFIX$ZIP_NAME ($SIZE)"
    # 上传成功 → 删除本地包（节省磁盘）
    rm -f "$ZIP_PATH"
    log "OK local zip removed (space saved)"
    # 云端轮换：只留最近 OSS_KEEP 份
    # 注意：凭据必须留到这里才 unset —— 之前提前 unset 导致 ls 静默失败、
    # 轮换从未生效（2026-11-10 才发现，OSS 里已堆到 9 份）
    STALE=$(aliyun oss ls "oss://$OSS_BUCKET/$OSS_PREFIX" \
              -e "$OSS_ENDPOINT" --region "$OSS_REGION" -s 2>/dev/null \
            | grep -oE 'vibe-code-backup-[0-9]{8}-[0-9]{6}\.zip' \
            | sort -u | head -n -"$OSS_KEEP")
    for OLD in $STALE; do
      aliyun oss rm "oss://$OSS_BUCKET/$OSS_PREFIX$OLD" \
        -e "$OSS_ENDPOINT" --region "$OSS_REGION" -f >> "$LOG" 2>&1 \
        && log "OK oss-rotate removed $OLD"
    done
  else
    log "FAILED oss upload: $(echo "$UP" | tail -2 | tr '\n' ' ')"
    # 上传失败 → 保留本地包，不删
    log "WARN local zip kept: $ZIP_PATH"
  fi
  unset ALIBABA_CLOUD_ACCESS_KEY_ID ALIBABA_CLOUD_ACCESS_KEY_SECRET
else
  log "SKIP oss（凭据未配置），本地包保留: $ZIP_PATH"
fi

exit 0
