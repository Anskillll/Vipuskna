#!/bin/sh
set -eu

backup_dir=/opt/backups/postgresql
timestamp="$(date +%Y%m%d-%H%M%S)"
backup_file="$backup_dir/medclinic-$timestamp.dump"

umask 077
mkdir -p "$backup_dir"
pg_dump --format=custom --file="$backup_file" medclinic
find "$backup_dir" -type f -name 'medclinic-*.dump' -mtime +14 -delete
