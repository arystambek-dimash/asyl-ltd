# Ключи ApiPay по отделам и «Удалённая оплата» только в POS

Статус: согласован в чате · 2026-09-15 · продолжение `2026-09-12-pos-design.md`

## Задача

1. На главной мобильной кассы убрать строку «Удаленная оплата». Сама функция
   остаётся во вкладке «Удаленно» внутри POS.
2. Общий ключ ApiPay (`APIPAY_API_KEY` / `APIPAY_WEBHOOK_SECRET` из `.env`)
   заменить на ключи, привязанные к отделам продаж. Ключ выдаёт суперюзер в
   настройке отдела («Отделы продаж»). Ключ отдела использует только этот
   отдел. В базе ключ хранится зашифрованным, наружу никогда не отдаётся.
3. Вебхук ApiPay приходит на один общий адрес, но каждое событие явно
   сопоставляется с отделом по секрету, которым оно подписано, и применяется
   только к счёту этого отдела.

## Границы

В скоупе: мобильная касса (главная), бэкенд ApiPay (создание счетов, отмена,
возвраты, сверка, вебхук), модель и API отдела, модалка «Отделы продаж»,
настройки/compose/README, миграция данных.

Вне скоупа: клиентский портал (продолжает выставлять QR и счёт на телефон
через тот же бэкенд), десктопная касса и страница долга, права на отделы
(остаётся `sys_permissions.manage` для названия/цвета), ротация ключей ApiPay
внутри кабинета провайдера.

## Факты о провайдере

По документации `apipay.kz/docs`: у организации может быть несколько API-ключей;
адрес вебхука и секрет подписи настраиваются на каждый ключ отдельно
(Settings → API keys). Подпись `X-Webhook-Signature: sha256=<hex>` — HMAC-SHA256
от сырого тела запроса секретом этого ключа.

## Решения

### Мобильная касса

- `HomeItemKey` становится равным `MobileMenuKey`; из `ITEMS`, `quick` и
  `subtitles` главной уходит `remote`. Кнопка POS в панели и вкладка
  «Удаленно» в POS не меняются; `?view=remote` по-прежнему открывает POS на
  вкладке «Удаленно».
- Тесты `mobile-cashier.test.tsx`: список кнопок главной без «Удаленная
  оплата»; проверка «без права принимать оплаты» больше не ищет эту строку.

### Хранение ключей отдела

`apps.sales.models.Department` получает поля:

| Поле | Тип | Смысл |
| --- | --- | --- |
| `apipay_api_key_encrypted` | `TextField(blank=True, default="")` | Fernet-токен ключа |
| `apipay_webhook_secret_encrypted` | `TextField(blank=True, default="")` | Fernet-токен секрета вебхука |
| `apipay_updated_at` | `DateTimeField(null=True, blank=True)` | когда суперюзер менял ключи |

Методы модели: `apipay_configured` (bool, есть ли ключ), `apipay_api_key`
и `apipay_webhook_secret` (расшифровка, `""` если пусто),
`set_apipay_api_key(value: str)` и `set_apipay_webhook_secret(value: str)`
(`""` очищает; оба ставят `apipay_updated_at = now()`). Очистка ключа очищает и
секрет: без ключа секрет бессмыслен.

Шифрование — `apps/common/crypto.py`: `encrypt_secret(str) -> str`,
`decrypt_secret(str) -> str`, исключение `SecretDecryptError`. Ключ Fernet
выводится как `urlsafe_b64(sha256("asyl-secret-store:" + SECRET_KEY))`;
через `MultiFernet` учитываются `SECRET_KEY_FALLBACKS`, если заданы. Смена
`SECRET_KEY` без fallback делает ключи нечитаемыми: суперюзер вводит их
заново, ошибка расшифровки трактуется как «ключ не настроен».

Библиотека `cryptography>=43,<47` добавляется в `requirements-prod.txt`.

### API отдела

`GET /api/departments/` и `/api/departments/{id}/` для всех сотрудников
отдают `apipay_configured: bool`, `apipay_webhook_configured: bool`,
`apipay_updated_at`. Только суперюзеру дополнительно `apipay_key_hint`
(`"••••ab12"`, последние 4 символа ключа; `""` если ключа нет).

`PATCH /api/departments/{id}/` принимает write-only поля `apipay_api_key` и
`apipay_webhook_secret` (строки, пробелы по краям срезаются, максимум 255
символов, минимум 8 для ключа). Правила:

- Любое из этих полей в теле запроса от не-суперюзера → 403
  `apipay_superuser_only`, даже с правом `sys_permissions.manage`.
- `apipay_api_key: ""` или `null` очищает ключ и секрет.
- `apipay_webhook_secret: ""` очищает только секрет.
- Непустой секрет без ключа (ни в запросе, ни в базе) → 400
  «Сначала укажите API-ключ отдела».
- Ответ никогда не содержит ключ или секрет.

Права на `list/retrieve/update` не меняются (`IsStaff` / `HasPerm`).

### Маршрутизация запросов к ApiPay

В `apps/orders/apipay.py`:

```python
@dataclass(frozen=True)
class ApiPayCredentials:
    api_key: str
    base_url: str
    department_id: int
    department_name: str

class ApiPayConfigurationError(RuntimeError):
    department_name: str  # "" если отдел не найден

def credentials_for_department(department) -> ApiPayCredentials
def credentials_for_department_code(code: str) -> ApiPayCredentials
def credentials_for_order(order) -> ApiPayCredentials
def credentials_for_invoice(record: ApiPayInvoice) -> ApiPayCredentials
def api_request(method, path, payload=None, *, credentials) -> dict
```

- `credentials_for_department_code` ищет `Department` по коду; отсутствие
  отдела или пустой ключ → `ApiPayConfigurationError` с именем отдела.
  Активность отдела не проверяется: старые счета отключённого отдела всё ещё
  сверяются его ключом.
- `create_invoice` берёт `credentials_for_order(order)` в фазе 1, до создания
  локальной записи `ApiPayInvoice`, чтобы отказ конфигурации не оставлял
  резерв `creating`. Так же `create_refund` (по заказу). `get_invoice`,
  `recover_invoice_issue_mapping`, `get_invoice_refunds`,
  `_cancel_invoice_locked` берут `credentials_for_invoice(record)`.
- `get_invoice(record)` вместо `get_invoice(invoice_id)`;
  `check_invoice_statuses(invoice_ids, *, credentials)`.
- Сверка (`reconcile_apipay_invoices`) аннотирует кандидатов кодом отдела
  (`payment__order__department`), группирует по нему и делает батчи внутри
  группы своим ключом. Группа без ключа считается упавшей
  (`stats.failed += len(группы)`, warning в лог), её `updated_at` не трогается.
- Ошибка для кассы: `_provider_error` возвращает 400 с прежним кодом
  `payment_provider_not_configured` и текстом «В отделе «Мельница» не подключён
  Kaspi (ApiPay)». Если отдел не найден: «У заказа не указан отдел продаж».
  Портал оставляет обезличенный текст «Счёт на оплату временно недоступен.»

### Вебхук

- `POST /api/webhooks/apipay/` остаётся единственным адресом. Подпись
  проверяется по секретам всех отделов, у которых секрет задан
  (`Department.objects.exclude(apipay_webhook_secret_encrypted="")`, порядок
  `created_at, id`). Совпавший отдел — отдел события.
- Ни у одного отдела нет секрета → 503 `webhook_not_configured` (как раньше).
  Ни один секрет не подошёл → 401 `invalid_signature`.
- Если счёт события уже известен локально и его заказ принадлежит другому
  отделу → 403 `invoice_department_mismatch`, событие не сохраняется, warning в
  лог. Сверка позже приведёт состояние в порядок ключом правильного отдела.
- `ApiPayWebhookEvent.department` — FK на `sales.Department` (`null=True`,
  `SET_NULL`, `related_name="apipay_webhook_events"`). При отложенном
  применении (`_replay_one_webhook`) счёт, найденный позже, тоже сверяется с
  отделом события: несовпадение откладывает событие с ошибкой
  `invoice_department_mismatch`.

### Общего ключа нет

- Из `config/_settings/base.py` уходят `APIPAY_API_KEY` и
  `APIPAY_WEBHOOK_SECRET`; `APIPAY_BASE_URL` и `APIPAY_TIMEOUT_SECONDS`
  остаются общими (адрес провайдера один).
- Миграция `sales.0004_seed_apipay_from_env` один раз переносит значения
  `os.environ["APIPAY_API_KEY"]` / `APIPAY_WEBHOOK_SECRET` в основной отдел
  (`is_default=True`, иначе первый активный), только если ни у одного отдела
  ключа ещё нет. Обратная миграция ничего не делает.
- В `docker-compose.yml` и `docker-compose.prod.yml` обе переменные остаются
  необязательными (`${APIPAY_API_KEY:-}`) с комментарием «только для
  разовой миграции; после первого деплоя можно удалить из `.env`».
  `deploy/tests/test_production_hardening.py` проверяет, что они больше не
  обязательны.
- README: раздел «ApiPay / Kaspi Pay» описывает ключи по отделам, общий адрес
  вебхука и то, что у каждого ключа в кабинете ApiPay указывается этот адрес.
- `config/observability.py` уже редактирует `apipay_api_key` и
  `apipay_webhook_secret` в логах; новые имена полей серилизатора совпадают.

### Модалка «Отделы продаж»

- `DepartmentManager` выносится из `app/orders/page.tsx` в
  `components/orders/department-manager.tsx` (вместе с `DEPARTMENT_COLORS`),
  поведение названия/цвета/основного/отключения прежнее.
- В строке отдела под счётчиком заказов: «Kaspi подключён» или «Kaspi не
  подключён» (по `apipay_configured`).
- Для суперюзера при редактировании существующего отдела ниже цвета
  появляется блок «Kaspi / ApiPay»:
  - статус: «Ключ ••••ab12 · вебхук настроен» / «Ключ ••••ab12 · без секрета
    вебхука» / «Не подключён»;
  - `PasswordInput` «API-ключ» и «Секрет вебхука» (пустые, `autoComplete="off"`);
  - адрес вебхука `${window.location.origin}/api/webhooks/apipay/` с кнопкой
    «Скопировать» и подсказкой «Укажите этот адрес у ключа в кабинете ApiPay»;
  - кнопка «Сохранить ключ» (активна, когда введён ключ или секрет) шлёт
    `PATCH /departments/{id}/` только с заполненными полями и очищает поля;
  - ссылка «Отключить Kaspi» (только если `apipay_configured`) шлёт
    `{ apipay_api_key: "" }`.
- Не-суперюзер блока не видит. Ключ никогда не подставляется в поля.
- Тип `Department` в `lib/types.ts` получает `apipay_configured: boolean`,
  `apipay_webhook_configured: boolean`, `apipay_key_hint?: string`,
  `apipay_updated_at: string | null`.

## Ошибки и коды

| Ситуация | Код | HTTP |
| --- | --- | --- |
| Не-суперюзер шлёт ключ/секрет | `apipay_superuser_only` | 403 |
| Секрет без ключа | `apipay_secret_without_key` | 400 |
| У отдела заказа нет ключа | `payment_provider_not_configured` | 400 (касса), 502 портал как раньше |
| Вебхук без единого секрета в системе | `webhook_not_configured` | 503 |
| Подпись не подошла ни одному отделу | `invalid_signature` | 401 |
| Счёт события принадлежит другому отделу | `invoice_department_mismatch` | 403 |

## Тесты

Бэкенд:

- `apps/common/tests/test_crypto.py`: шифрование/расшифровка, разные токены
  для одного значения, чужой ключ → `SecretDecryptError`, fallback-ключ.
- `apps/sales/tests/test_department_apipay.py`: суперюзер задаёт ключ и
  секрет → `apipay_configured`, `apipay_key_hint`, ключа в ответе нет;
  менеджер с `sys_permissions.manage` → 403; очистка `""`; секрет без ключа →
  400; список для обычного сотрудника без `apipay_key_hint`; ключ в базе не
  равен открытому тексту.
- `apps/orders/tests/test_apipay.py` и соседние (`test_staff_pos_qr`,
  `test_payment_regressions`, `test_apipay_reconciliation`,
  `test_apipay_refund_reconciliation`, `portal/tests/test_portal_actions`)
  переводятся с `settings.APIPAY_*` на фикстуру `apipay_department`
  (отдел `main` с ключом и секретом). Новые проверки: два отдела шлют разные
  `X-API-Key`; отдел без ключа → `ApiPayConfigurationError` и оплата
  отклонена без записи `ApiPayInvoice`; сверка группирует по отделам (два
  батча с разными ключами); вебхук секретом отдела B по счёту отдела A → 403;
  у принятого события заполнен `department`.
- `deploy/tests/test_production_hardening.py`: переменные ApiPay не обязательны.

Фронт:

- `mobile-cashier.test.tsx`: главная без «Удаленная оплата», POS-вкладки
  прежние.
- `components/orders/department-manager.test.tsx`: суперюзер видит блок и
  статус; сохранение шлёт `PATCH` с ключом и секретом и очищает поля;
  «Отключить Kaspi» шлёт `{ apipay_api_key: "" }`; не-суперюзер блока не видит;
  ключ не отображается.

Финал: `npm run check`, `npm run build`, полный pytest бэкенда и deploy;
коммит, пуш ветки.
