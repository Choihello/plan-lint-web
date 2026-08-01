#!/bin/sh
# 컨테이너를 non-root로 돌리되, 런타임에 root 소유로 마운트되는 볼륨은 쓸 수 있어야 한다.
#
# Fly 볼륨(/data)은 root:root 755로 마운트되므로 USER 지시자만 쓰면 앱이 쿼터 DB를
# 쓰지 못하고, 그러면 비용 상한이 조용히 무력화된다. 그래서 root로 시작해 DB 디렉터리
# 소유권만 넘기고 즉시 권한을 내려놓는다.
set -e

DB_PATH="${PLW_QUOTA_DB:-/tmp/quota.sqlite3}"
DB_DIR=$(dirname "$DB_PATH")

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DB_DIR"
  chown -R appuser:appuser "$DB_DIR" || echo "warn: $DB_DIR 소유권 변경 실패 — 쿼터 쓰기를 확인하세요" >&2
  exec setpriv --reuid=appuser --regid=appuser --init-groups "$@"
fi

exec "$@"
