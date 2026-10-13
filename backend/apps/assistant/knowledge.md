# Как устроена CRM «Асыл»

Мукомольное предприятие. Продаёт муку и отруби в мешках клиентам (фурами и
вагонами), принимает зерно (вагоны и машины) в силосы, считает мешки камерами.
Валюты — тенге (KZT, ₸) и доллар (USD, $). Часовой пояс — Asia/Almaty.

## Карта основных таблиц

- Клиенты — `clients_client`. Имя клиента хранится в его пользователе:
  `accounts_user.first_name || ' ' || last_name` (или `username`), название
  ТОО/ИП — `clients_client.company_name`. В документах клиент называется
  `company_name`, а если оно пустое — именем. Магазины клиента — `clients_store`.
- Отделы продаж — `clients_department` (модель sales.Department). Заказ хранит
  код отдела строкой: `orders_order.department = clients_department.code`.
- Сотрудники — `employees_employee` (+ `accounts_user`), права — `rbac_permission`.
- Товары — `catalog_product` (название, цвет мешка, вес мешка `weight_kg`),
  договорные цены клиентов — `catalog_clientprice`.
- Заказы — `orders_order`, позиции — `orders_orderitem`, оплаты —
  `orders_payment`, возвраты денег — `orders_paymentrefund`.
- Отгрузка заказа — `shipments_shipment` (одна на заказ, `shipped_at` — момент
  отгрузки), вагоны вагонного заказа — `shipments_shipmentwagon`, с каких складов
  ушли мешки — `shipments_shipmentsource`.
- Склады готовой продукции — `warehouse_warehouse`, остатки мешков —
  `warehouse_stockitem.bags`, история движений — `warehouse_stockmovement`
  (`delta` — изменение, `reason` — причина).
- Возврат мешков от клиента — `orders_goodsreturn` и `orders_goodsreturnitem`
  (только склад, без денег и без заказов).
- Зерно: поставки — `grain_grainsupply`, рейсы — `grain_wagon` (и вагоны, и машины:
  каждый рейс — строка этой таблицы), силосы — `grain_silo`, движения зерна —
  `grain_grainmovement`.
- Камеры: 24/7-счёт мешков производства по дням — `cameras_alwaysondailyanalytics`
  (`model_total + adjustment` = мешков за день по камере), счёт на отгрузке —
  `cameras_shippingdailyanalytics`.
- Задачи сотрудникам — `tasks_task`, журнал событий — `eventlog_eventlog`.

## Продажи и мешки

Сколько мешков продано или отгружено (всего, по товарам, клиентам, дням) — как в
«Отчётах»: сумма `orders_orderitem.quantity` по позициям отгруженных заказов
(`status = 'shipped'`). У старых отгруженных заказов строки `shipments_shipment` нет:
присоединяй её только LEFT JOIN, а день продажи — `coalesce(s.shipped_at, o.created_at)`.
Обычный JOIN с отгрузкой теряет такие заказы. Товар позиции —
`orders_orderitem.product_id` (`catalog_product.name`). Не бери для этого
`shipments_shipment.bags_loaded` (сколько насчитала камера при погрузке — только для
сверки погрузки) и `shipments_shipmentwagon` (вагоны из отчёта о вагонах).

## Удалённое не существует

Заказ в корзине удалён для всех отчётов. В КАЖДОМ запросе, где участвует заказ
(напрямую или через позиции, оплаты, отгрузку), фильтруй
`orders_order.deleted_at IS NULL AND orders_order.purged_at IS NULL`. Оплаты, позиции и
отгрузки удалённых заказов тоже не считаются: даже в вопросе только про оплаты
присоединяй `orders_order` и ставь этот фильтр.
Удалённые заказы показывай, только если о них спросили прямо («что в корзине»).

## Статусы заказа

{order_statuses}

Финансовые статусы (входят в оборот, выручку и долг) — все, кроме
{non_financial_statuses}. Ждут отгрузки (мешки обещаны, со склада ещё не
списаны): {awaiting_shipment_statuses}. Отгружен: `shipped`.

Вид транспорта `orders_order.transport_type`: {transport_types}.

## Деньги заказа

- Сумма заказа = Σ по позициям `(quantity - returned_quantity) * coalesce(unit_price, 0)`.
  Бонусные позиции (`is_bonus`) бесплатные — цена 0.
- Оплачено = Σ `greatest(amount - refunded_amount, 0)` по оплатам заказа со
  статусом `confirmed`. Остальные оплаты ещё не деньги.
- Остаток = сумма − оплачено.
- Долг клиента — остаток по ОТГРУЖЕННЫМ заказам (`status = 'shipped'`), где
  сумма > оплачено. Неотгруженный заказ долгом не считается.
- `orders_order.payment_status`: unpaid — не оплачен, partial — частично,
  settled — оплачен полностью.
- Валюта у заказа своя (`orders_order.currency`). НИКОГДА не складывай тенге и
  доллары: группируй суммы по валюте и называй валюту в ответе.
- День продажи = `coalesce(shipments_shipment.shipped_at, orders_order.created_at)`.
- Деньги в кассе за период считаются по дате подтверждения оплаты
  `orders_payment.confirmed_at` (если её нет — `paid_at`), только `status = 'confirmed'`.

Этапы оплаты `orders_payment.status`: {payment_statuses}.
Способы `orders_payment.method`: {payment_methods}.

## Склад и зерно

- Остаток мешков товара на складе — `warehouse_stockitem.bags`; вес в тоннах =
  мешки × `catalog_product.weight_kg` / 1000.
- Нетто рейса зерна хранится в `grain_wagon.net_weight_kg` (кг). Направление
  `direction`: `intake` — приход зерна (въехал гружёным), `passage` — вывоз
  отрубей (въехал пустым). Нетто уже посчитано с учётом направления — не
  пересчитывай его из `gross_weight_kg` и `tare_weight_kg`.
- Заполненность силоса — последний `grain_grainmovement.balance_after_kg` по силосу
  против `grain_silo.total_capacity_kg`.

## Готовые формулы SQL

Сумма, оплачено и остаток каждого живого заказа. Позиции и оплаты считай
отдельными подзапросами: JOIN позиций и оплат в одном запросе размножает строки
и завышает суммы.

```sql
WITH order_money AS (
  SELECT o.id, o.client_id, o.currency, o.status, o.created_at,
         coalesce((SELECT sum((i.quantity - i.returned_quantity) * coalesce(i.unit_price, 0))
                   FROM orders_orderitem i WHERE i.order_id = o.id), 0) AS total,
         coalesce((SELECT sum(greatest(p.amount - p.refunded_amount, 0))
                   FROM orders_payment p WHERE p.order_id = o.id AND p.status = 'confirmed'), 0) AS paid
  FROM orders_order o
  WHERE o.deleted_at IS NULL AND o.purged_at IS NULL
)
-- Долг по валютам:
SELECT currency, sum(total - paid) AS debt
FROM order_money WHERE status = 'shipped' AND total > paid GROUP BY currency
```

Имя клиента, как в документах:

```sql
coalesce(nullif(trim(c.company_name), ''), nullif(trim(u.first_name || ' ' || u.last_name), ''), u.username)
-- FROM clients_client c JOIN accounts_user u ON u.id = c.user_id
```

- Отгружено за период: позиции отгруженных заказов, у которых
  `coalesce(s.shipped_at, o.created_at)` попадает в период
  (`LEFT JOIN shipments_shipment s ON s.order_id = o.id`), мешки — `sum(i.quantity)`.
- Оплаты за период: `coalesce(p.confirmed_at, p.paid_at)::date`, `p.status = 'confirmed'`,
  заказ оплаты живой (`JOIN orders_order o ON o.id = p.order_id` + фильтр корзины).
- Товар в истории заказов — `orders_orderitem.product_label_snapshot` (подпись на момент
  заказа), текущий каталог — `catalog_product.name`.
