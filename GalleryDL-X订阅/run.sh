#!/bin/sh
# 手动单订阅抓取：run.sh <url> [first_range] [save_videos] [save_metadata]
# 由 Python 调度器内部等价逻辑调用时，直接运行 scheduler；本脚本仅供手动 docker exec 使用。
URL="$1"; RANGE="${2:-0}"; VIDS="${3:-true}"; META="${4:-true}"
[ -z "$URL" ] && { echo "usage: run.sh <url> [first_range] [save_videos] [save_metadata]"; exit 1; }
if [ "$META" = "true" ]; then
  PP='[{"name":"metadata","filename":"{author[name]}_{tweet_id}.json"}]'
else
  PP='[]'
fi
cat > /tmp/gdl_over.json <<EOF
{"extractor":{"twitter":{"videos":$VIDS,"postprocessors":$PP}}}
EOF
ARGS="-c /config/config.json -c /tmp/gdl_over.json"
if [ -n "$RANGE" ] && [ "$RANGE" != "0" ]; then
  ARGS="$ARGS --range 1-$RANGE"
fi
echo "===== $(date '+%F %T') manual run: $URL range=$RANGE vids=$VIDS meta=$META ====="
gallery-dl $ARGS "$URL" 2>&1
echo "===== $(date '+%F %T') finished ====="