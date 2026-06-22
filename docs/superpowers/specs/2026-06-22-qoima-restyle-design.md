# Рестайл под qoima: палитра + шрифт

**Дата:** 2026-06-22
**Статус:** утверждён к реализации

## Цель

Привести визуальный стиль asyl-ltd CRM к стилю qoima-crm: тёплая Notion-палитра,
синий акцент `#2383e2`, шрифт Inter. Меняем только **значения** CSS-переменных и
подключаем шрифт — имена токенов и классы компонентов не трогаем, поэтому все
страницы и компоненты (все на `var(--*)`) преображаются автоматически.

## Подход

asyl и qoima — оба Tailwind v4 + CSS-переменные + cva-компоненты. asyl сохраняет
свои имена токенов (`--background`, `--foreground`, `--primary`, `--border`,
`--ring`, `--sidebar-*` и т.д.); мы переопределяем их значения палитрой qoima.

**Ключевое:** primary-кнопки остаются ТЁМНЫМИ (ink), как у qoima; синий
`#2383e2` идёт в `--ring` (фокус/обводки) и `--sidebar-ring` — это и даёт
«фирменный синий» qoima без перекраски всех кнопок в синий.

## Маппинг — LIGHT (`:root`)

| asyl токен | значение | источник qoima |
|---|---|---|
| `--background` | `#ffffff` | canvas |
| `--foreground` | `#37352f` | ink |
| `--card` / `--popover` | `#ffffff` | canvas |
| `--card-foreground` / `--popover-foreground` | `#37352f` | ink |
| `--primary` | `#37352f` | ink (тёмные primary-кнопки) |
| `--primary-foreground` | `#ffffff` | canvas |
| `--secondary` | `#f7f6f3` | surface-2 |
| `--secondary-foreground` | `#37352f` | ink |
| `--muted` | `#f7f6f3` | surface-2 |
| `--muted-foreground` | `#787671` | ink-3 |
| `--accent` | `#f7f6f3` | surface-2 |
| `--accent-foreground` | `#37352f` | ink |
| `--destructive` | `#d8473a` | danger |
| `--destructive-foreground` | `#ffffff` | |
| `--success` | `#3d9c47` | success |
| `--warning` | `#d49a2a` | warn |
| `--border` | `#ececea` | hairline |
| `--input` | `#e2e1de` | hairline-strong |
| `--ring` | `#2383e2` | **accent (синий)** |
| `--sidebar` | `#fbfbfa` | surface |
| `--sidebar-foreground` | `#37352f` | ink |
| `--sidebar-primary` | `#37352f` | ink |
| `--sidebar-primary-foreground` | `#ffffff` | |
| `--sidebar-accent` | `#efeeec` | surface-3 (активный пункт) |
| `--sidebar-accent-foreground` | `#37352f` | ink |
| `--sidebar-border` | `#ececea` | hairline |
| `--sidebar-ring` | `#2383e2` | accent |

`--radius` остаётся `0.625rem` (близко к qoima `rounded-lg`). Графики
(`--chart-1..5`) оставляем как есть — на тему не влияют.

## Маппинг — DARK (`.dark`)

| asyl токен | значение | источник qoima dark |
|---|---|---|
| `--background` | `#191919` | canvas |
| `--foreground` | `#e6e3dc` | ink |
| `--card` | `#202020` | surface |
| `--card-foreground` | `#e6e3dc` | ink |
| `--popover` | `#252525` | surface-2 |
| `--popover-foreground` | `#e6e3dc` | ink |
| `--primary` | `#e6e3dc` | ink (светлые кнопки на тёмном) |
| `--primary-foreground` | `#191919` | canvas |
| `--secondary` | `#252525` | surface-2 |
| `--secondary-foreground` | `#e6e3dc` | ink |
| `--muted` | `#252525` | surface-2 |
| `--muted-foreground` | `#9b9a94` | ink-4 |
| `--accent` | `#2f2f2f` | surface-3 |
| `--accent-foreground` | `#e6e3dc` | ink |
| `--destructive` | `#d8473a` | danger |
| `--destructive-foreground` | `#ffffff` | |
| `--success` | `#3d9c47` | |
| `--warning` | `#d49a2a` | |
| `--border` | `#2a2a2a` | hairline |
| `--input` | `#373737` | hairline-strong |
| `--ring` | `#4ea4ee` | **accent (синий, светлее)** |
| `--sidebar` | `#202020` | surface |
| `--sidebar-foreground` | `#e6e3dc` | ink |
| `--sidebar-primary` | `#e6e3dc` | ink |
| `--sidebar-primary-foreground` | `#191919` | |
| `--sidebar-accent` | `#2f2f2f` | surface-3 |
| `--sidebar-accent-foreground` | `#e6e3dc` | ink |
| `--sidebar-border` | `#2a2a2a` | hairline |
| `--sidebar-ring` | `#4ea4ee` | accent |

Золотой акцент asyl уходит; синий — везде (light `#2383e2`, dark `#4ea4ee`).
`--success`/`--warning` добавляются в `.dark` (сейчас их там нет — наследуются;
задаём явно для согласованности).

## Шрифт

В `frontend/src/app/layout.tsx` подключить **Inter** через `next/font/google`:
- `const inter = Inter({ subsets: ["latin", "cyrillic"], variable: "--font-sans", display: "swap" })`
- Навесить `inter.variable` на `<html>` (className).
В `globals.css` `body` — `font-family: var(--font-sans), -apple-system, system-ui, sans-serif;`
и базовый размер `font-size: 15px; line-height: 1.5;` (как у qoima).

JetBrains Mono для tabular-чисел — вне scope (числа уже идут через классы
`tabular-nums`); добавить можно позже.

## Файлы

- `frontend/src/app/globals.css` — значения `:root` и `.dark` (имена токенов и
  `@theme inline`-маппинг не трогаем).
- `frontend/src/app/layout.tsx` — подключение Inter + className на `<html>`.

## Тестирование

- `npm run build` — собирается без ошибок.
- Визуальная проверка в Docker (`docker compose up --build`): дашборд, заказы,
  пост отгрузки — синий фокус/обводки, тёплый фон, Inter, light + dark.
- Юнит-тестов на CSS нет.

## Вне scope (YAGNI)

- Имена токенов qoima (`--ink`/`--canvas`) — не переносим, asyl-имена остаются.
- Подгонка размеров/теней компонентов под qoima (только палитра+шрифт).
- JetBrains Mono.
- 9 тег-цветов qoima — не добавляем (asyl Badge использует свои tone).
- Изменения логики `feat/weights` (счёт по классам) — не затрагиваются.
