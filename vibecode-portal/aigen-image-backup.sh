#!/bin/bash
# aigen 图片备份（2026-11-18 建）
# 三层：
#   daily   每日 rsync 硬链接快照 footprint-images + pose-out → backups/images/daily/<date>/（留14，省空间）
#   weekly  每周日 footprint+pose-out+aigen.db一致性快照+aigen应用 → tar.gz → OSS backups/aigen/（留8，传完删本地）
#   2026-11-25 拆分：aigen.db 主库已迁至 /home/admin/apps/vibecode-aigen/aigen.db（本脚本快照源已改指新库）
#   monthly 每月1日 om-library（静态2.3G）→ tar → OSS backups/aigen-omlib/（留2，传完删本地）
# 复用门户既有 OSS 基础设施（aliyun CLI + .secrets 凭据 + 新加坡内网端点免流量费）。
# cron: 15 3 * * *  （auto 模式：每日快照；周日加 weekly；1日加 monthly）
set -u

WS="$HOME/.openclaw/workspace"
APP="$HOME/apps/vibecode-aigen"
HTML="/usr/share/nginx/html"
SECRETS="$WS/.secrets"
BK="$WS/backups"
IMG_BK="$BK/images"
DAILY_DIR="$IMG_BK/daily"
LOG="$BK/aigen-image-backup.log"
STAMP=$(date +%Y%m%d-%H%M%S)
TODAY=$(date +%Y%m%d)
DOW=$(date +%u)        # 1=Mon .. 7=Sun
DOM=$(date +%d)        # 01..31

OSS_BUCKET="snowhook-openclaw"
OSS_ENDPOINT="oss-ap-southeast-1-internal.aliyuncs.com"
OSS_REGION="ap-southeast-1"
WEEKLY_PREFIX="backups/aigen/"
WEEKLY_KEEP=8
OMLIB_PREFIX="backups/aigen-omlib/"
OMLIB_KEEP=2
DAILY_KEEP=14

mkdir -p "$DAILY_DIR" "$IMG_BK"
log(){ echo "[$(date '+%F %T')] $*" >> "$LOG"; }

oss_creds(){
  if [ -r "$SECRETS/ALIYUN_OSS_AK_ID" ] && [ -r "$SECRETS/ALIYUN_OSS_AK_SECRET" ]; then
    export ALIBABA_CLOUD_ACCESS_KEY_ID="$(cat "$SECRETS/ALIYUN_OSS_AK_ID")"
    export ALIBABA_CLOUD_ACCESS_KEY_SECRET="$(cat "$SECRETS/ALIYUN_OSS_AK_SECRET")"
    return 0
  fi
  return 1
}
oss_unset(){ unset ALIBABA_CLOUD_ACCESS_KEY_ID ALIBABA_CLOUD_ACCESS_KEY_SECRET; }

# 上传一个本地包到 OSS 前缀，成功后删本地包并轮换云端旧份
oss_upload_rotate(){
  local LOCAL="$1" PREFIX="$2" KEEP="$3" NAME_RE="$4"
  local BASENAME; BASENAME=$(basename "$LOCAL")
  local UP UP_RC SIZE
  SIZE=$(du -h "$LOCAL" | cut -f1)
  UP=$(aliyun oss cp "$LOCAL" "oss://$OSS_BUCKET/$PREFIX" -e "$OSS_ENDPOINT" --region "$OSS_REGION" -f 2>&1)
  UP_RC=$?
  if [ $UP_RC -eq 0 ]; then
    log "OK oss://$OSS_BUCKET/$PREFIX$BASENAME ($SIZE)"
    rm -f "$LOCAL"; log "OK local removed $BASENAME"
    local STALE OLD
    STALE=$(aliyun oss ls "oss://$OSS_BUCKET/$PREFIX" -e "$OSS_ENDPOINT" --region "$OSS_REGION" -s 2>/dev/null \
            | grep -oE "$NAME_RE" | sort -u | head -n -"$KEEP")
    for OLD in $STALE; do
      aliyun oss rm "oss://$OSS_BUCKET/$PREFIX$OLD" -e "$OSS_ENDPOINT" --region "$OSS_REGION" -f >> "$LOG" 2>&1 \
        && log "OK oss-rotate removed $OLD"
    done
  else
    log "FAILED oss upload $BASENAME: $(echo "$UP" | tail -2 | tr '\n' ' ')"
    log "WARN local kept $LOCAL"
  fi
}

do_daily(){
  log "START daily snapshot"
  local DEST="$DAILY_DIR/$TODAY"
  if [ -d "$DEST" ]; then log "SKIP daily (today exists)"; return 0; fi
  # 找最近一份已有快照作硬链接基准
  local PREV; PREV=$(ls -1d "$DAILY_DIR"/2* 2>/dev/null | sort | tail -1)
  local LINK=""
  [ -n "$PREV" ] && [ "$PREV" != "$DEST" ] && LINK="--link-dest=$PREV/footprint-images"
  mkdir -p "$DEST"
  # footprint-images
  if [ -d "$HTML/footprint-images" ]; then
    rsync -a --delete $LINK "$HTML/footprint-images/" "$DEST/footprint-images/" 2>>"$LOG" \
      && log "OK daily footprint-images" || log "WARN daily footprint rsync rc=$?"
  fi
  # pose-out（单独 link-dest）
  local LINK2=""
  [ -n "$PREV" ] && [ "$PREV" != "$DEST" ] && LINK2="--link-dest=$PREV/pose-out"
  if [ -d "$HTML/pose-out" ]; then
    rsync -a --delete $LINK2 "$HTML/pose-out/" "$DEST/pose-out/" 2>>"$LOG" \
      && log "OK daily pose-out" || log "WARN daily pose-out rsync rc=$?"
  fi
  local SZ; SZ=$(du -sh "$DEST" 2>/dev/null | cut -f1)
  log "OK daily snapshot $DEST ($SZ, hardlink base=${PREV:-none})"
  # 轮换：留最近 DAILY_KEEP 份
  local OLD_DIR
  ls -1d "$DAILY_DIR"/2* 2>/dev/null | sort | head -n -"$DAILY_KEEP" | while read -r OLD_DIR; do
    rm -rf "$OLD_DIR" && log "OK daily-rotate removed $(basename "$OLD_DIR")"
  done
}

do_weekly(){
  log "START weekly OSS backup"
  local STAGING="/tmp/aigen-weekly-$STAMP"
  local TAR="/tmp/aigen-backup-$STAMP.tar.gz"
  rm -rf "$STAGING"; mkdir -p "$STAGING"
  # 图片
  rsync -a "$HTML/footprint-images" "$STAGING/" 2>/dev/null
  rsync -a "$HTML/pose-out" "$STAGING/" 2>/dev/null
  # aigen 应用（含 data/ 原生库元数据、index.html、assets、thumbs；39M）
  rsync -a "$HTML/aigen" "$STAGING/" 2>/dev/null
  # aigen.db 一致性快照（主库在 vibecode-aigen app 目录）
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$APP/aigen.db" ".backup '$STAGING/aigen.db'" && log "OK aigen.db snapshot" \
      || { cp -a "$APP/aigen.db" "$STAGING/aigen.db" 2>/dev/null; log "WARN aigen.db raw copy"; }
  else
    cp -a "$APP/aigen.db" "$STAGING/aigen.db" 2>/dev/null
  fi
  # routing.db 一致性快照（KV 镜像，回滚用）
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$WS/routing.db" ".backup '$STAGING/routing.db'" 2>/dev/null && log "OK routing.db snapshot"
  fi
  cat > "$STAGING/RESTORE.txt" << 'META'
aigen 周备包还原步骤
─────────────────────
footprint-images/  → /usr/share/nginx/html/footprint-images/（用户上传+生成库存图）
pose-out/          → /usr/share/nginx/html/pose-out/（AI 生成结果图）
aigen/             → /usr/share/nginx/html/aigen/（前端应用：index.html/assets/data/thumbs）
aigen.db           → /home/admin/apps/vibecode-aigen/aigen.db（统一存储元数据，一致性快照；2026-11-25 拆分后主库在此，systemd 服务 vibecode-aigen 读它）
routing.db         → /home/admin/.openclaw/workspace/routing.db（旧 KV 镜像，回滚用）
还原后：chown -R admin:admin 上述目录；systemctl restart routing-server；nginx -t && systemctl reload nginx
注意：om-library（原生库 2.3G）不在周备包，见月度包 backups/aigen-omlib/。
META
  tar -czf "$TAR" -C /tmp "aigen-weekly-$STAMP" 2>>"$LOG"
  local RC=$?
  rm -rf "$STAGING"
  if [ $RC -ne 0 ]; then log "FAILED weekly tar rc=$RC"; rm -f "$TAR"; return 1; fi
  log "OK weekly tar $(du -h "$TAR" | cut -f1)"
  if oss_creds; then
    oss_upload_rotate "$TAR" "$WEEKLY_PREFIX" "$WEEKLY_KEEP" 'aigen-backup-[0-9]{8}-[0-9]{6}\.tar\.gz'
    oss_unset
  else
    log "SKIP weekly oss (no creds), local kept $TAR"
  fi
}

do_monthly(){
  log "START monthly om-library OSS backup"
  local TAR="/tmp/aigen-omlib-$STAMP.tar"
  # om-library 图片已压缩（webp/jpg），不再 gzip，省 CPU
  tar -cf "$TAR" -C "$HTML" om-library 2>>"$LOG"
  local RC=$?
  if [ $RC -ne 0 ]; then log "FAILED monthly tar rc=$RC"; rm -f "$TAR"; return 1; fi
  log "OK monthly tar $(du -h "$TAR" | cut -f1)"
  if oss_creds; then
    oss_upload_rotate "$TAR" "$OMLIB_PREFIX" "$OMLIB_KEEP" 'aigen-omlib-[0-9]{8}-[0-9]{6}\.tar'
    oss_unset
  else
    log "SKIP monthly oss (no creds), local kept $TAR"
  fi
}

MODE="${1:-auto}"
case "$MODE" in
  daily)   do_daily ;;
  weekly)  do_weekly ;;
  monthly) do_monthly ;;
  auto)
    do_daily
    [ "$DOW" = "7" ] && do_weekly
    [ "$DOM" = "01" ] && do_monthly
    ;;
  *) echo "usage: $0 {daily|weekly|monthly|auto}"; exit 2 ;;
esac
log "DONE mode=$MODE"
exit 0