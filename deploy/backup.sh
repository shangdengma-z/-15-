#!/usr/bin/env bash
# 每日备份：MySQL（账号/会话/配额）+ SQLite 档案库 + 结果缓存，保留最近 14 份。
#
#   crontab -e 里加一行（每天 03:40）：
#   40 3 * * * cd /srv/bisai && bash deploy/backup.sh >> /var/log/circle15-backup.log 2>&1
#
# 两份都要备：账号在 MySQL 里，体检结果在 SQLite 里，缺一个就只剩空壳。
# 缓存可以不备（重跑会重新出网，只是烧配额），但备了省事。
set -euo pipefail

cd "$(dirname "$0")/.."
STAMP="$(date +%Y%m%d-%H%M)"
OUT="backup/$STAMP"
COMPOSE=(docker compose --project-directory . -f deploy/docker-compose.prod.yml)

mkdir -p "$OUT" backup/_live

# 口令从 .env 取，不写进脚本、不出现在 ps 里
DB_USER_NAME="$(awk -F= '/^DB_USER=/{print $2}' .env)"
DB_NAME_VAL="$(awk -F= '/^DB_NAME=/{print $2}' .env)"
"${COMPOSE[@]}" exec -T db mysqldump -u "$DB_USER_NAME" --single-transaction --routines \
  "$DB_NAME_VAL" | gzip > "$OUT/mysql.sql.gz"

# SQLite 用 .backup API 而不是 cp：服务在写的时候 cp 出来的可能是半截事务。
# 容器里 /srv/backup 就是宿主机 backup/（见 docker-compose.prod.yml 的挂载）
"${COMPOSE[@]}" exec -T backend python - <<'PY'
import sqlite3, pathlib
for p in pathlib.Path("data/db").glob("*.db"):
    con = sqlite3.connect(p)
    dst = sqlite3.connect(f"/srv/backup/_live/{p.name}")
    con.backup(dst)
    dst.close(); con.close()
PY
mv backup/_live "$OUT/sqlite"

tar czf "$OUT.tar.gz" -C backup "$STAMP" && rm -rf "$OUT"

# 只留 14 天：2G 的机器磁盘通常只有 20G，备份涨满会把 Docker 挤死
ls -1t backup/*.tar.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
echo ">> 备份完成：$OUT.tar.gz ($(du -h "$OUT.tar.gz" | cut -f1))"
