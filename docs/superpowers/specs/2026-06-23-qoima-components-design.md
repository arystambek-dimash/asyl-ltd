# Рестайл компонентов под qoima: карточки, таблицы, бейджи, stat-карточки

**Дата:** 2026-06-23
**Статус:** утверждён к реализации

## Цель

Привести базовые UI-компоненты asyl к стилю qoima (Notion-стиль): мягкие
карточки с тонкой тенью, плотные таблицы с hover и sortable-заголовками,
Notion-мягкие бейджи с опцией «точка», новые stat-карточки (с синей акцентной),
тонкие прогресс-бары и фильтр-пилюли. Применить на странице **Заказы** как
эталон; остальные списки подхватят перестиль Card/Table/Badge автоматически.

## Ограничения совместимости (важно)

- qoima использует свои токены (`bg-canvas`, `text-ink-3`, `bg-tag-blue-bg`,
  `shadow-card`). У asyl токены — `var(--card)`, `var(--muted-foreground)`,
  `var(--border)`, `var(--ring)`. **Классы qoima адаптируются под наши токены**,
  не копируются дословно. Значения уже совпадают (палитра смержена ранее).
- **Сохранить имена экспортов** `Card/CardHeader/CardTitle/CardDescription/
  CardContent/CardFooter`, `Table/THead/TBody/TR/TH/TD`, `Badge` — их импортируют
  17 страниц. Менять только внутренние классы.
- **Badge: сохранить тоны** `muted/primary/success/warning/destructive/outline`
  (от них зависит `StatusBadge` и `ORDER_STATUS_TONE`). Добавить опциональный
  проп `dot`. Не менять сигнатуру `tone`.

## Компоненты

### Card (`ui/card.tsx`) — рестайл
- Обёртка: `rounded-xl border bg-[var(--card)] text-[var(--card-foreground)]` +
  мягкая тень qoima через утилиту `.shadow-card` (добавить в globals.css):
  `0 1px 0 0 rgba(15,15,15,.04), 0 1px 3px 0 rgba(15,15,15,.06)`.
- Остальные подкомпоненты (Header/Title/Content/Footer) — без изменений API,
  только при необходимости отступы.

### Table (`ui/table.tsx`) — рестайл
- `Table`: обёртка `overflow-x-auto`, `border-separate border-spacing-0`.
- `THead`: `text-[12px] font-medium text-[var(--muted-foreground)]`.
- `TR`: `transition-colors hover:bg-[var(--muted)]/50` + нижние границы ячеек
  `[&>td]:border-b [&>td]:border-[var(--border)]`.
- `TH`: `h-9 px-3 sm:px-4 text-left align-middle font-medium text-[var(--muted-foreground)]`.
- `TD`: `h-12 px-3 sm:px-4 align-middle`.
- `TBody` сохраняется (asyl его экспортирует и использует).

### Badge (`ui/badge.tsx`) — рестайл + `dot`
- Notion-мягкий вид: `inline-flex items-center gap-1 px-2 h-[22px] text-[12px]
  rounded-md font-medium leading-none`.
- Тоны те же (значения мягче): `muted` (серый), `primary` (синий мягкий),
  `success`/`warning`/`destructive` (мягкий фон через `/12`), `outline`.
- Новый проп `dot?: boolean` — точка `h-1.5 w-1.5 rounded-full` цвета тона
  (через `currentColor`/inline-цвет).

### StatCard (`ui/stat-card.tsx`) — новый
```
{ label, value, accent?, caption? }
```
- Карточка: `bg-[var(--card)] border border-[var(--border)] rounded-lg p-4 sm:p-5
  flex flex-col gap-3 transition-colors hover:border-[var(--ring)]/40`.
- Accent-вариант: фон `bg-[var(--ring)]/10 border-[var(--ring)]/20`, число цвета
  `var(--ring)`.
- Число: `text-[24px] sm:text-[30px] leading-[1.1] tracking-tight tabular-nums`.
- Существующий `kpi-card.tsx` оставляем (он на Card) — НЕ удаляем, новые карточки
  используют StatCard.

### ProgressBar (`ui/progress-bar.tsx`) — новый
```
{ pct, className? }
```
- Контейнер `h-1 bg-[var(--muted)] rounded-full overflow-hidden`.
- Fill `rounded-full transition-all`, цвет `bg-[var(--success)]` при pct>=100,
  иначе `bg-[var(--ring)]`; ширина `min(pct,100)%` инлайном.

### FilterPills (`ui/filter-pills.tsx`) — новый
```
items: {key, label, count}[]; active; onChange
```
- Группа: `flex bg-[var(--muted)] border border-[var(--border)] rounded-md p-0.5`.
- Пилюля: `h-7 px-2.5 inline-flex items-center gap-1.5 text-[13px] rounded
  transition-colors`; активная `bg-[var(--card)] text-[var(--foreground)]
  shadow-sm font-medium`, неактивная `text-[var(--muted-foreground)]
  hover:text-[var(--foreground)]`; counter `text-[11px] tabular-nums`.

### SortableHeader (`ui/sortable-header.tsx`) — новый
```
{ label, sortKey, activeKey, dir, onClick, align? }
```
- Рендерит `TH` с кнопкой: `inline-flex items-center gap-1 hover:text-[var(--foreground)]`.
- Иконки lucide: активная `ArrowUp`/`ArrowDown` по dir, неактивная
  `ArrowUpDown` `opacity-40`. Поддержка `align="right"` (flex-row-reverse).

## Эталонная страница: Заказы (`app/orders/page.tsx`)

- Ряд **StatCard** сверху: «Всего заказов» / «В процессе» (status != shipped/cancelled)
  / «Сумма» (accent, сумма total_amount). `grid sm:grid-cols-3 gap-3`.
- **FilterPills** по статусу заказа (все / по основным статусам) + поиск-инпут
  (по клиенту/номеру/#id), как у qoima.
- Таблица с **SortableHeader** (№/Клиент/Сумма/Статус), сорт по выбранному ключу,
  `StatusBadge` с точкой. Двухстрочной ячейки клиента у нас нет данных — оставляем
  одну строку (имя клиента), без выдумывания полей.
- NewOrderForm (модалка) — без изменений.

## Файлы

- Рестайл: `ui/card.tsx`, `ui/table.tsx`, `ui/badge.tsx`, `globals.css` (утилита
  `.shadow-card`).
- Новые: `ui/stat-card.tsx`, `ui/progress-bar.tsx`, `ui/filter-pills.tsx`,
  `ui/sortable-header.tsx`.
- Эталон: `app/orders/page.tsx`.

## Тестирование

- `npm run build` — без ошибок и типовых ошибок.
- Визуальная проверка в Docker (`docker compose up --build`): Заказы (stat-карточки,
  пилюли, сортировка, бейджи-точки), плюс одна страница из подхвативших перестиль
  (напр. Клиенты) — проверить, что Card/Table/Badge выглядят по-новому и ничего не
  сломалось. Light + dark.
- Юнит-тестов на CSS/верстку нет.

## Вне scope (YAGNI)

- Перевод всех списков на StatCard/пилюли/сорт (только Заказы-эталон; остальные
  подхватят базовый перестиль).
- 9 Notion-тонов и доска/board-вид.
- Двухстрочные ячейки с email (нет таких данных в asyl-заказах).
- Удаление `kpi-card.tsx`.
- Изменения бэкенда и логики `feat/weights`.
