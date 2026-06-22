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

## Подключение камер к вебхуку

Сервер и камеры — в одной локальной сети цеха. Камера шлёт HTTP POST на
**локальный IP сервера**.

1. Узнайте IP сервера (на машине с Docker):
   - macOS: `ipconfig getifaddr en0`
   - Linux: `hostname -I`
   Например `192.168.1.50`.
2. Зарегистрируйте камеру: фронт → **Управление → Камеры → Добавить камеру**
   (название, ID вида `gate-01`, тип, шаблон ответа). Скопируйте показанный
   **ключ** (он показывается один раз).
3. Настройте камеру/контроллер на запрос:

```
POST http://192.168.1.50:8000/api/webhook/camera/
Content-Type: application/json
X-Camera-Key: <ключ_камеры>

{ "camera_id": "gate-01", "plate": "777ABC02" }
```
Счётчик добавляет `"bags": 50`, выезд — `"weight_kg": 10500`.

Пример проверки из терминала:
```bash
curl -X POST http://192.168.1.50:8000/api/webhook/camera/ \
  -H "Content-Type: application/json" \
  -H "X-Camera-Key: <ключ>" \
  -d '{"camera_id":"gate-01","plate":"777ABC02"}'
```
Сервер ответит по шаблону камеры, напр. `{"open": true, "order": 4}`.

> `ALLOWED_HOSTS=*` в compose разрешает доступ по IP в локальной сети. Для
> публичного домена задайте конкретные хосты.
