#!/bin/bash
# 动画化解题档案定期备份（2026-09-24 建；同日增加 OSS 异地上传）
# 备份内容：apps/vibecode-math/data/math_videos（record.json/题目图片/动画工程/成片）
#   2026-11-25 拆分：math_videos 已从 workspace 迁至 /home/admin/apps/vibecode-math/data/
#          + /usr/share/nginx/html/math-exports（自包含 HTML 存档）
# 本地保留最近 KEEP 份轮换；另上传 OSS bucket snowhook-openclaw（新加坡内网端点，免流量费）
# 凭据：.secrets/ALIYUN_OSS_AK_ID / ALIYUN_OSS_AK_SECRET（0600，不经过聊天记录）；日志 backup.log
set -u
BK_DIR="$HOME/backups/math"
SECRETS="$HOME/.openclaw/workspace/.secrets"
OSS_BUCKET="snowhook-openclaw"
OSS_PREFIX="backups/math/"
OSS_ENDPOINT="oss-ap-southeast-1-internal.aliyuncs.com"
OSS_REGION="ap-southeast-1"
OSS_KEEP=14
KEEP=3
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$BK_DIR/math-archives-$STAMP.tar.gz"
LOG="$BK_DIR/backup.log"

mkdir -p "$BK_DIR" || { echo "[$(date '+%F %T')] mkdir failed" >> "$LOG" 2>/dev/null; exit 1; }

tar -czf "$OUT" \
  -C "$HOME/apps/vibecode-math/data" math_videos \
  -C /usr/share/nginx/html math-exports \
  >> "$LOG" 2>&1
RC=$?

if [ $RC -eq 0 ]; then
  SIZE=$(du -h "$OUT" | cut -f1)
  echo "[$(date '+%F %T')] OK local $OUT ($SIZE)" >> "$LOG"
else
  echo "[$(date '+%F %T')] FAILED tar rc=$RC" >> "$LOG"
  rm -f "$OUT"
  exit $RC
fi

# ── OSS 异地上传（凭据缺失时跳过，不影响本地备份）──
if [ -r "$SECRETS/ALIYUN_OSS_AK_ID" ] && [ -r "$SECRETS/ALIYUN_OSS_AK_SECRET" ]; then
  export ALIBABA_CLOUD_ACCESS_KEY_ID="$(cat "$SECRETS/ALIYUN_OSS_AK_ID")"
  export ALIBABA_CLOUD_ACCESS_KEY_SECRET="$(cat "$SECRETS/ALIYUN_OSS_AK_SECRET")"
  UP=$(aliyun oss cp "$OUT" "oss://$OSS_BUCKET/$OSS_PREFIX" -e "$OSS_ENDPOINT" --region "$OSS_REGION" -f 2>&1)
  if [ $? -eq 0 ]; then
    echo "[$(date '+%F %T')] OK oss://$OSS_BUCKET/$OSS_PREFIX$(basename "$OUT")" >> "$LOG"
    # 上传成功 → 删除本地包节省磁盘（2026-09-24 用户要求；失败时保留本地）
    rm -f "$OUT" && echo "[$(date '+%F %T')] OK local removed after oss upload" >> "$LOG"
    # 云端轮换：只留最近 OSS_KEEP 份（时间戳文件名升序排列，取最旧的删）
    STALE=$(aliyun oss ls "oss://$OSS_BUCKET/$OSS_PREFIX" -e "$OSS_ENDPOINT" --region "$OSS_REGION" -s 2>/dev/null \
      | grep -oE 'math-archives-[0-9]{8}-[0-9]{6}\.tar\.gz' | sort -u | head -n -"$OSS_KEEP")
    for OLD in $STALE; do
      aliyun oss rm "oss://$OSS_BUCKET/$OSS_PREFIX$OLD" -e "$OSS_ENDPOINT" --region "$OSS_REGION" -f >> "$LOG" 2>&1 \
        && echo "[$(date '+%F %T')] OK oss-rotate removed $OLD" >> "$LOG"
    done
  else
    echo "[$(date '+%F %T')] OSS UPLOAD FAILED: $(echo "$UP" | tail -2 | tr '\n' ' ')" >> "$LOG"
  fi
  unset ALIBABA_CLOUD_ACCESS_KEY_ID ALIBABA_CLOUD_ACCESS_KEY_SECRET
else
  echo "[$(date '+%F %T')] SKIP oss（凭据未配置）" >> "$LOG"
fi

# 本地轮换：只留最近 KEEP 份
ls -1t "$BK_DIR"/math-archives-*.tar.gz 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f
exit 0
