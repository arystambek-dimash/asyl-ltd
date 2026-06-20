# АСЫЛ-LTD — Система учёта цеха

Внутренняя CRM мукомольного цеха «Асыл-LTD»: учёт сырья и склада, заказы,
отгрузки, клиентский портал.

## Стек

- **Бэкенд** (`backend/`): Django + Django REST Framework + PostgreSQL, JWT.
- **Фронтенд** (`frontend/`): Next.js 15 + Tailwind 4 (дизайн-система shadcn/OKLCH, светлая тема).
- **Инфраструктура**: Docker Compose (db + backend + frontend).

## Запуск всей системы (Docker)

```bash
docker compose up --build
```

После старта:

- Фронтенд: <http://localhost:3000>
- API: <http://localhost:8000/api>
- Django-админка: <http://localhost:8000/admin>

При первом запуске автоматически:
- применяются миграции,
- сидируются группы ролей (`manager`, `accountant`, `operator`, `boss`),
- создаётся суперпользователь **admin / admin12345** (поменяйте в проде).

### Первые шаги

1. Войдите как `admin` на <http://localhost:3000/login>.
2. Заведите справочники (сорта, фасовки) и товары в Django-админке
   (<http://localhost:8000/admin>) или через API.
3. Создайте пользователей и назначьте им роли (группы) в админке.
4. Клиентам создайте учётную запись с галкой «is_client» и привяжите её к
   карточке клиента — тогда им откроется портал.

## Роли

| Роль | Доступ |
|------|--------|
| Менеджер | клиенты, товары, заказы |
| Бухгалтер | оплаты, отчёты по долгам |
| Оператор | пост отгрузки (arrive/load/ship) |
| Начальник | всё + разрешение отгрузки в долг |
| Админ | полный доступ + админка |
| Клиент | портал: каталог, свои заказы |

## Разработка (без Docker)

**Бэкенд:**
```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
pytest          # 40 тестов
```

**Фронтенд:**
```bash
cd frontend
npm install
npm run dev     # http://localhost:3000
```

Документация дизайна и план — в `docs/superpowers/`.
