#!/bin/sh
set -e

echo "Ожидание PostgreSQL ($DB_HOST:$DB_PORT)…"
until pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" >/dev/null 2>&1; do
  sleep 1
done
echo "PostgreSQL готов."

python manage.py migrate --noinput

# Seed a demo superuser on first run (idempotent).
python manage.py shell <<'PY'
import os
from django.contrib.auth import get_user_model
U = get_user_model()
name = os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")
pwd = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "admin12345")
if not U.objects.filter(username=name).exists():
    U.objects.create_superuser(username=name, password=pwd)
    print(f"Создан суперпользователь: {name}")
else:
    print(f"Суперпользователь {name} уже существует.")
PY

exec "$@"
