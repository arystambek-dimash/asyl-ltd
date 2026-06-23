# Переработка каталога: только Товары + списание по факту CV

**Дата:** 2026-06-23
**Статус:** утверждён к реализации
**База:** ветка `feat/weights` (там уже есть `cv_class`, `counts_by_class`, per-class Redis-счёт)

## Цель

Свернуть каталог в одну сущность **Товар**: убрать отдельные Сорта/Фасовки,
сделать у товара поля «название (сорт) + цвет (тип) + фасовка 25/50 + цена +
статус». CV-класс вычисляется из цвета+веса. В UI остаётся одна страница
«Товары» (группа «Номенклатура» убирается). При отгрузке склад списывается по
**фактическому подсчёту CV** (`counts_by_class`), а не по заказу.

## Секция 1: Модель Product

`Product` становится самодостаточным (FK Grade/Packaging убираются):

```python
class Product(models.Model):
    COLORS = [("Red", "Красный"), ("Green", "Зелёный"), ("Blue", "Синий")]
    WEIGHTS = [(Decimal("25"), "25 кг"), (Decimal("50"), "50 кг")]

    name = models.CharField(max_length=100)            # сорт/название
    color = models.CharField(max_length=10, choices=COLORS)
    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, choices=WEIGHTS)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("name", "color", "weight_kg")

    @property
    def cv_class(self):                                # Red + 50 → "Red_50"
        w = "50" if self.weight_kg == Decimal("50") else "25"
        return f"{self.color}_{w}"

    def __str__(self):
        return f"{self.name} · {dict(self.COLORS)[self.color]} {int(self.weight_kg)} кг"
```

- `weight_kg` теперь реальное поле (было property от packaging).
- `cv_class` — вычисляемое property (совместимо с CV-моделью и `counts_by_class`).
- `__str__`/label: «Высший сорт · Красный 50 кг».
- **Grade и Packaging удаляются** (модели, миграции-таблицы, сериализаторы, вьюхи, роуты, страницы, админ).

## Секция 2: Миграция данных + бэкенд

### Миграции (порядок)
1. Добавить поля `name`, `color`, новый `weight_kg` на Product (временно nullable;
   старый FK-граф ещё на месте).
2. Data-миграция переноса: для каждого Product —
   `name = grade.name`; `weight_kg = packaging.weight_kg`;
   `color` из существующего `cv_class` (`Red_50`→`Red`); если `cv_class` пуст —
   маппинг по имени grade (Красный→Red, Зелёный→Green, Синий→Blue), иначе `Red`
   как дефолт.
3. Удалить FK `grade`, `packaging`, старый `cv_class`-CharField; убрать старый
   `unique_together`. Сделать `name`/`color`/`weight_kg` not-null, добавить новый
   `unique_together = (name, color, weight_kg)`.
4. Удалить модели Grade, Packaging (drop tables) отдельной финальной миграцией.

(Старая seed-миграция `0003_seed_bag_classes` остаётся в истории как есть —
данные она уже создала; переносит их шаг 2. Новых seed не добавляем.)

### Бэкенд-правки
- `catalog/models.py` — новая Product; удалить Grade, Packaging.
- `catalog/serializers.py` — `ProductSerializer` поля:
  `id, name, color, color_label (get_color_display), weight_kg, price, is_active,
  label (__str__), cv_class (read-only property)`. Удалить Grade/Packaging-сериализаторы.
- `catalog/views.py` / `urls.py` — оставить только `ProductViewSet`; убрать
  `grades`/`packagings` роуты и вьюсеты.
- `catalog/admin.py` — убрать Grade/Packaging-регистрацию; Product list_display
  `name, color, weight_kg, price, is_active`.
- **Warehouse-сериализатор** (`warehouse/serializers.py`) — критично: сейчас
  читает `product.grade.name`/`product.packaging.name`. Меняем:
  `grade`-поле (оставляем имя ключа для совместимости фронта StockItem) ←
  `product.name`; `packaging` ← человекочитаемая фасовка (`{int(weight)} кг`);
  `weight_kg` ← `product.weight_kg`; добавляем `color`/`color_label`.
  (Либо переименовать поля и поправить фронт StockItem-тип — выбрать минимально
  ломающий вариант: оставить ключи `grade`/`packaging`, наполнить новыми данными.)
- **Orders** — `OrderItem` использует `product.cv_class` (теперь property, ок) и
  `product.weight_kg` (теперь поле, ок); `product_label` через `__str__` (ок).
  Менять не требуется.

## Секция 3: Списание склада по факту CV

### Поведение `record_shipment` (shipments/services.py)
- Основной путь: брать `counts_by_class` (`{"Red_50": 12, ...}`) из связанной с
  заказом VideoJob (последняя done-задача; `counts_by_class` уже сохраняется на
  `feat/weights`). Для каждого класса:
  - найти `Product` по `cv_class` (распарсить `Red_50` → `color="Red"`,
    `weight=50`; матчить по color+weight; если несколько товаров с разным `name`
    — взять товар из позиций заказа этого класса, иначе первый активный);
  - списать `deduct_stock(product, count, allow_negative=True)`.
- **Fallback**: если у заказа нет VideoJob с непустым `counts_by_class` (грузили
  вручную) — списывать по `OrderItem`, как сейчас (`deduct_stock` без negative).
- Сравнение веса (выезд−въезд vs мешки×вес) остаётся как доп-сверка.

### `deduct_stock` (warehouse/services.py)
Сейчас жёстко запрещает недостачу (`insufficient_stock`). Добавить параметр
`allow_negative=False`:
- при `allow_negative=True` пропускать списание даже если `item.bags < bags`
  (остаток уходит в минус), и логировать предупреждение в eventlog
  (`event_type="stock_negative"`).
- при `allow_negative=False` — прежнее поведение (raise).
- Если `StockItem` для продукта вообще нет при `allow_negative=True` — создать с
  нулём и списать в минус (или пропустить с предупреждением — создаём с минусом).

## Фронтенд

- `lib/types.ts` — `Product`: `{ id, name, color, color_label, weight_kg, price,
  is_active, label, cv_class }`. Убрать `grade`/`packaging`. Поправить
  `StockItem`, если ключи менялись (по решению в Секции 2 ключи `grade`/`packaging`
  сохраняем — StockItem не трогаем, только наполнение меняется).
- Удалить страницы `app/catalog/grades`, `app/catalog/packagings`.
- `app/catalog/products/page.tsx` — форма создания: `name` (текст), `color`
  (select Красный/Зелёный/Синий), `weight_kg` (select 25/50), `price`. Колонки
  таблицы: Название, Цвет, Фасовка, Цена, Статус. Inline-edit цены и toggle
  активности — сохранить. Payload: `{ name, color, weight_kg, price }`.
- `sidebar.tsx` — убрать группу «Номенклатура» с детьми; добавить один пункт
  «Товары» (`/catalog/products`, icon Package, perm `catalog.view`) в раздел
  «Работа».
- Места, где создавался заказ с выбором товара (`portal/orders/new`,
  `orders` NewOrderForm) — список товаров теперь по `label`; selha не ломается
  (label уже использовался).

## Тестирование

- Backend: миграция переноса (старый товар с grade/packaging+cv_class →
  name/color/weight); `cv_class` property (`Red_50`); `unique_together`;
  ProductSerializer поля; warehouse-сериализатор отдаёт name/фасовку.
- `deduct_stock(allow_negative=True)` — уходит в минус + лог; `False` — raise.
- `record_shipment` по `counts_by_class` — списывает нужные товары; fallback на
  OrderItem без видео; недостача → минус + предупреждение, отгрузка проходит.
- Обновить существующие тесты, создающие Grade/Packaging (catalog/orders/
  warehouse/shipments — ~8 файлов): заменить на новый Product(name/color/weight).
- Frontend `npm run build` + визуальная проверка: одна страница Товары, создание
  с цветом/фасовкой, навбар без «Номенклатуры».
- Docker `up --build` + `migrate --check`.

## Вне scope (YAGNI)
- Произвольный вес (только 25/50).
- Отдельная сущность цвета/типа (color — choices-поле).
- Изменение CV-пакета / воркера (cv_class формат тот же).
- Ручное редактирование `counts_by_class`.
