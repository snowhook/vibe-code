#!/bin/bash
# ai-pose 成品图目录清理：保留最近 30 天、最多 200 张（2026-11-13 建）
set -u
DIR=/usr/share/nginx/html/pose-out
[ -d "$DIR" ] || exit 0
KEEP_DAYS=30
MAX_FILES=200
before=$(find "$DIR" -type f -name 'ark_*.png' | wc -l)
size_before=$(du -sm "$DIR" 2>/dev/null | cut -f1)
find "$DIR" -type f -name 'ark_*.png' -mtime +$KEEP_DAYS -delete
n=$(find "$DIR" -type f -name 'ark_*.png' | wc -l)
if [ "$n" -gt "$MAX_FILES" ]; then
  find "$DIR" -type f -name 'ark_*.png' -printf '%T@ %p\n' | sort -n | head -n $((n-MAX_FILES)) | cut -d' ' -f2- | xargs -r rm -f
fi
after=$(find "$DIR" -type f -name 'ark_*.png' | wc -l)
size_after=$(du -sm "$DIR" 2>/dev/null | cut -f1)
echo "$(date '+%F %T') files $before -> $after, size ${size_before:-0}MB -> ${size_after:-0}MB"
