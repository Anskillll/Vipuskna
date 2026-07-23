#!/usr/bin/env bash
set -Eeuo pipefail

APP=/opt/medclinic
STAMP=$(date +%Y%m%d-%H%M%S)
MIGRATION_DIR=/opt/backups/sqlite-migration-$STAMP

mkdir -p "$MIGRATION_DIR"
chmod 700 "$MIGRATION_DIR"
cp -a "$APP/.env" "$MIGRATION_DIR/env.before-postgresql"
cp -a "$APP/db.sqlite3" "$MIGRATION_DIR/db.sqlite3"
chmod 600 "$MIGRATION_DIR"/*

python3 - "$MIGRATION_DIR/db.sqlite3" <<'PY'
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
result = connection.execute("PRAGMA integrity_check").fetchone()[0]
connection.close()
if result != "ok":
    raise SystemExit(f"SQLite integrity check failed: {result}")
PY

database_password=$(openssl rand -hex 32)
services_stopped=0
environment_switched=0

rollback() {
    exit_code=$?
    if [ "$exit_code" -ne 0 ]; then
        if [ "$environment_switched" -eq 1 ]; then
            cp "$MIGRATION_DIR/env.before-postgresql" "$APP/.env"
            chown medclinic:medclinic "$APP/.env"
            chmod 600 "$APP/.env"
        fi
        if [ "$services_stopped" -eq 1 ]; then
            systemctl restart medclinic medclinic-bot || true
        fi
        echo "Migration failed; recovery copy: $MIGRATION_DIR" >&2
    fi
    exit "$exit_code"
}
trap rollback EXIT

systemctl stop medclinic-bot medclinic
services_stopped=1
cp -a "$APP/db.sqlite3" "$MIGRATION_DIR/db.final.sqlite3"
chmod 600 "$MIGRATION_DIR/db.final.sqlite3"

sudo -u medclinic env DJANGO_DB_ENGINE=sqlite \
    "$APP/.venv/bin/python" "$APP/manage.py" dumpdata \
    --natural-foreign \
    --exclude contenttypes \
    --exclude auth.permission \
    --indent 2 > "$MIGRATION_DIR/data.json"
chmod 600 "$MIGRATION_DIR/data.json"

cat > "$MIGRATION_DIR/model_counts.py" <<'PY'
import json

from django.apps import apps

excluded = {"contenttypes.ContentType", "auth.Permission"}
counts = {}
for model in apps.get_models():
    if model._meta.label in excluded or model._meta.proxy or not model._meta.managed:
        continue
    counts[model._meta.label] = model._default_manager.count()
print(json.dumps(counts, sort_keys=True))
PY
chmod 600 "$MIGRATION_DIR/model_counts.py"

sudo -u medclinic env DJANGO_DB_ENGINE=sqlite \
    "$APP/.venv/bin/python" "$APP/manage.py" shell \
    < "$MIGRATION_DIR/model_counts.py" \
    | tail -n 1 > "$MIGRATION_DIR/counts.sqlite.json"
chmod 600 "$MIGRATION_DIR/counts.sqlite.json"

sudo -u postgres psql \
    -v ON_ERROR_STOP=1 \
    --set=dbpass="$database_password" \
    > "$MIGRATION_DIR/postgres-role.log" <<'SQL'
SELECT format('CREATE ROLE medclinic LOGIN PASSWORD %L', :'dbpass')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'medclinic') \gexec
SELECT format('ALTER ROLE medclinic WITH LOGIN PASSWORD %L', :'dbpass') \gexec
SQL

if sudo -u postgres psql -tAc \
    "SELECT 1 FROM pg_database WHERE datname='medclinic'" | grep -q 1; then
    echo "PostgreSQL database medclinic already exists; refusing to overwrite it." >&2
    exit 1
fi
sudo -u postgres createdb --owner=medclinic --encoding=UTF8 medclinic

run_postgresql_manage() {
    sudo -u medclinic env \
        DJANGO_DB_ENGINE=postgresql \
        DJANGO_DB_NAME=medclinic \
        DJANGO_DB_USER=medclinic \
        DJANGO_DB_PASSWORD="$database_password" \
        DJANGO_DB_HOST=127.0.0.1 \
        DJANGO_DB_PORT=5432 \
        DJANGO_DB_CONN_MAX_AGE=60 \
        "$APP/.venv/bin/python" "$APP/manage.py" "$@"
}

run_postgresql_manage migrate --noinput > "$MIGRATION_DIR/migrate.log"
run_postgresql_manage shell -c \
    "from django.contrib.sites.models import Site; Site.objects.all().delete()" \
    > /dev/null
run_postgresql_manage loaddata --format json - \
    < "$MIGRATION_DIR/data.json" \
    > "$MIGRATION_DIR/loaddata.log"
run_postgresql_manage shell \
    < "$MIGRATION_DIR/model_counts.py" \
    | tail -n 1 > "$MIGRATION_DIR/counts.postgresql.json"
chmod 600 "$MIGRATION_DIR/counts.postgresql.json"

if ! cmp -s \
    "$MIGRATION_DIR/counts.sqlite.json" \
    "$MIGRATION_DIR/counts.postgresql.json"; then
    diff -u \
        "$MIGRATION_DIR/counts.sqlite.json" \
        "$MIGRATION_DIR/counts.postgresql.json" >&2 || true
    exit 1
fi

python3 - "$APP/.env" "$database_password" <<'PY'
from pathlib import Path
import os
import sys
import tempfile

path = Path(sys.argv[1])
password = sys.argv[2]
updates = {
    "DJANGO_DB_ENGINE": "postgresql",
    "DJANGO_DB_NAME": "medclinic",
    "DJANGO_DB_USER": "medclinic",
    "DJANGO_DB_PASSWORD": password,
    "DJANGO_DB_HOST": "127.0.0.1",
    "DJANGO_DB_PORT": "5432",
    "DJANGO_DB_CONN_MAX_AGE": "60",
}
lines = path.read_text(encoding="utf-8").splitlines()
seen = set()
result = []
for line in lines:
    key = (
        line.split("=", 1)[0]
        if "=" in line and not line.lstrip().startswith("#")
        else None
    )
    if key in updates:
        result.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        result.append(line)
for key, value in updates.items():
    if key not in seen:
        result.append(f"{key}={value}")

descriptor, temporary_name = tempfile.mkstemp(prefix=".env.", dir=path.parent)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(result) + "\n")
    os.chmod(temporary_name, 0o600)
    os.replace(temporary_name, path)
finally:
    if os.path.exists(temporary_name):
        os.unlink(temporary_name)
PY
chown medclinic:medclinic "$APP/.env"
chmod 600 "$APP/.env"
environment_switched=1

mkdir -p /opt/backups/postgresql
chown postgres:postgres /opt/backups/postgresql
chmod 700 /opt/backups/postgresql
install \
    -o root \
    -g postgres \
    -m 0750 \
    "$APP/deploy/backup-postgresql.sh" \
    /usr/local/sbin/medclinic-backup-postgresql

systemctl enable --now medclinic-db-backup.timer > /dev/null
systemctl start medclinic medclinic-bot
sleep 4
systemctl is-active --quiet medclinic
systemctl is-active --quiet medclinic-bot

sudo -u medclinic "$APP/.venv/bin/python" "$APP/manage.py" check \
    > "$MIGRATION_DIR/django-check.log"
curl -fsS --max-time 15 https://lclinic-ua.duckdns.org/ > /dev/null

systemctl start medclinic-db-backup.service
latest_backup=$(
    find /opt/backups/postgresql \
        -maxdepth 1 \
        -type f \
        -name 'medclinic-*.dump' \
        -printf '%T@ %p\n' \
        | sort -nr \
        | head -n 1 \
        | cut -d' ' -f2-
)
test -n "$latest_backup"
sudo -u postgres pg_restore --list "$latest_backup" > /dev/null

printf 'migration=ok\n'
printf 'backup_dir=%s\n' "$MIGRATION_DIR"
printf 'services=%s,%s\n' \
    "$(systemctl is-active medclinic)" \
    "$(systemctl is-active medclinic-bot)"
printf 'database='
sudo -u medclinic "$APP/.venv/bin/python" "$APP/manage.py" shell \
    -c "from django.db import connection; print(connection.vendor)" \
    | tail -n 1
printf 'counts='
cat "$MIGRATION_DIR/counts.postgresql.json"
printf 'backup=%s\n' "$latest_backup"

trap - EXIT
