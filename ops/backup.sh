#!/usr/bin/env bash
# Ubuntu で毎日実行：DB とサムネイルをバックアップ（14日分残す）
# crontab 例: 15 2 * * * $HOME/dwg-find/ops/backup.sh >> $HOME/dwg-find-backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
DEST=${BACKUP_DIR:-$HOME/dwg-find-backup}
STAMP=$(date +%Y%m%d-%H%M)
mkdir -p "$DEST"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$DEST/db-$STAMP.dump"
tar -czf "$DEST/media-$STAMP.tgz" -C data media
find "$DEST" -name 'db-*.dump' -mtime +14 -delete
find "$DEST" -name 'media-*.tgz' -mtime +14 -delete
echo "$(date '+%F %T') backup ok: $DEST/db-$STAMP.dump"
