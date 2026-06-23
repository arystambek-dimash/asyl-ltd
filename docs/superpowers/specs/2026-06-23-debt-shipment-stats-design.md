# Метки долга, отчёты по долгам и итог отгрузки по весу

**Дата:** 2026-06-23
**Статус:** утверждён к реализации
**База:** main

## Цель

1. Переделать весовой флоу: на выезде **не сравнивать** (убрать красный блок), а
   показать **итог отгрузки** на детальной странице заказа после статуса
   `shipped` — вес въезда/выезда/груза, посчитано камерой (`bags_loaded`),
   отгружено по весу (`net ÷ вес_фасовки`), заказано.
2. Помечать **долг/неоплату** в трёх местах: список заказов, отчёты (раздел
   долгов + кто разрешил долг), карточка клиента (общий долг).

## Секция 1: Весовой флоу и итог отгрузки

**Пост отгрузки (`shipping/page.tsx`):**
- Убрать компонент `WeightCompare` со стадии выезда. На выезде остаётся только
  поле «вес выезда» + кнопка «Отгрузить (выезд)».

**Бэкенд (`record_shipment`, уже считает `net = |выезд − въезд|`):** без изменений
логики; net и bags_loaded уже сохраняются.

**OrderSerializer** — гарантировать, что отдаются поля для итога:
`weigh_in_kg`, `weigh_out_kg`, `net_weight_kg`, `bags_loaded`, `bag_estimate_kg`.
Плюс отдать **вес одной фасовки** для расчёта «отгружено по весу»: добавить
`bag_weight_kg` = вес мешка первой позиции заказа (`items.first().product.weight_kg`,
"0" если позиций нет).

**Фикс `get_bag_estimate_kg`:** считать «ожидалось/факт по камере» от
**посчитанных** мешков, а не заказанных:
```python
def get_bag_estimate_kg(self, obj):
    s = self._shipment(obj)
    bags = s.bags_loaded if s else 0
    per = obj.items.first().product.weight_kg if obj.items.exists() else Decimal("0")
    return str(bags * per)
```

**Детальная заказа (`orders/[id]/page.tsx`):** при `order.status === "shipped"` —
карточка «Итог отгрузки»:
- Вес въезда: `weigh_in_kg` · Вес выезда: `weigh_out_kg` · Вес груза: `net_weight_kg`
- Посчитано камерой: `bags_loaded` меш.
- Отгружено по весу: `net_weight_kg ÷ bag_weight_kg` меш. (округление до целого; «—»
  если `bag_weight_kg` 0)
- Заказано: сумма `items[].quantity` меш.
- Расхождения: камера − по весу; заказ − по весу (информативно).

## Секция 2: Метки долга

**Бэкенд:**
- `OrderSerializer` — добавить `debt_override_by_name` (SerializerMethodField:
  `obj.debt_override_by.username` или ФИО сотрудника, null если не было override).
- `ClientSerializer` — добавить `debt_total` (SerializerMethodField): сумма
  `total_amount − paid_total` по заказам клиента, где `!is_fully_paid` и
  `status != "cancelled"`; "0" если долга нет.

**Список заказов (`orders/page.tsx`):** в строке, где `!is_fully_paid && status
!= "cancelled"`, рядом со `StatusBadge` — бейдж: «В долг» (warning) если
`debt_override`, иначе «Долг» (destructive). Оплаченные — без метки.

**Отчёты (`reports/page.tsx`):** в существующей таблице «Дебиторская
задолженность» добавить колонку «Разрешил долг» (`debt_override_by_name ?? "—"`)
и строку-итог с суммарным остатком по всем должникам.

**Карточка клиента (`clients/page.tsx`):** колонка «Долг» в таблице =
`debt_total` (красный текст если > 0, «—» если 0). Тип `Client` пополнить
`debt_total?: string`.

## Тестирование

- Backend: `get_bag_estimate_kg` от bags_loaded; `bag_weight_kg`;
  `debt_override_by_name` (имя при override / null без); `ClientSerializer.debt_total`
  (сумма остатков; 0 при полной оплате; cancelled не считается). 123 теста зелёные.
- Frontend: `npm run build` + Docker: на выезде нет сравнения; итог отгрузки на
  детальной при shipped; бейджи долга в заказах; колонка «Разрешил долг» + итог в
  отчётах; колонка «Долг» в клиентах. Light + dark.

## Вне scope (YAGNI)
- Новая таблица/endpoint отчётов (всё на `/orders/`, `/clients/`).
- Изменение CV-подсчёта.
- Новое поле «отгружено по весу» в БД (считается на фронте из net + bag_weight_kg).
- Хранение истории долга (только текущее состояние из заказов).
