#!/bin/sh
set -e

echo "Ожидание PostgreSQL ($DB_HOST:$DB_PORT)…"
until pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" >/dev/null 2>&1; do
  sleep 1
done
echo "PostgreSQL готов."

python manage.py migrate --noinput

# Истёкшие refresh-токены сессий входа (с их записями в чёрном списке):
# каждая ротация добавляет строки, иначе таблицы только растут.
python manage.py flushexpiredtokens

if [ "${DJANGO_COLLECTSTATIC:-0}" = "1" ]; then
  python manage.py collectstatic --noinput
fi

# Создать суперпользователя из SUPER_ADMIN_EMAIL / SUPER_ADMIN_PASS (идемпотентно).
python manage.py create_superuser_env

exec "$@"
