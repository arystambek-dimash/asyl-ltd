# Промпты для генерации SVG-ассетов карты территории

Каждый объект карты — отдельный файл в `assets/`. Код рисует все подписи,
цифры и живые данные сам; **от ИИ нужна только графика без текста**.
Сгенерировали красивее — вставили в файл ассета, карта осталась живой.

---

## 1. Общий стиль (вставляйте этот блок в КАЖДЫЙ промпт)

> Flat vector illustration, top-down 2.5D factory-map style (slight bird's-eye,
> like a game map), soft rounded shapes, subtle drop shadow below the object,
> clean pastel industrial palette: light steel #dfe3e9–#c9cfd8, warm grain
> yellow #f0c860→#d9a83f, paper bags #f2e2c4 with #d8c294 outline, asphalt
> #c9ced6, grass background #e3ead9 (or transparent), accent blue #2f6fdd,
> KUKA orange #ff8a00. No text, no letters, no numbers, no logos. Single
> object centered, transparent background, crisp SVG vector output.

Если инструмент отдаёт только PNG — генерите PNG побольше (2048px), затем
векторизация: recraft.ai / vectorizer.ai, чистка в svgomg (jakearchibald.github.io/svgomg).

## 2. Мастер-промпт всей сцены (референс-картинка «как на фото»)

> Flat vector map of a flour mill industrial complex, top-down 2.5D view,
> pastel colors, grass field #e3ead9 with a light ring road, railway line with
> sleepers along the top edge and a brown grain wagon on it, three steel grain
> silos with golden grain level visible inside, a green-roofed mill building
> with an orange KUKA robot arm stacking flour bags on a wooden pallet, a big
> warehouse split into four sections filled with rows of paper flour bags, an
> inclined bag conveyor from the warehouse up to the wagon, a truck weighbridge
> with a small digital display, a loading dock with a blue-cab truck under a
> conveyor, employee parking with three cars, a small security gatehouse with a
> red-white barrier, a canteen and an office building, small purple CCTV camera
> markers around the site. Soft shadows, rounded corners, clean UI-illustration
> style, no text anywhere.

Эта картинка — только референс стиля. В код вставляются ассеты по одному (ниже).

## 3. Ассеты по файлам

| Файл | Что генерить | Пропорции (виртуальный холст) | Что оставляет код |
| --- | --- | --- | --- |
| `silo-park.tsx` | ОДНА стальная цистерна с куполом, вертикальная | 64×188 (узкая, высокая) | волна зерна внутри clipPath, %, тонны, номер |
| `mill.tsx` | корпус мельницы с зелёной крышей + робот-манипулятор KUKA + паллета с мешками | 300×220 | подпись, счётчики, анимация руки |
| `warehouse.tsx` | крыша склада на 4 секции (вид сверху) + мешок 22×18 как элемент | 360×440 | сетка мешков по остаткам, счётчики |
| `gate.tsx` | будка КПП с окнами + шлагбаум красно-белый | 130×92 | подпись, анимация шлагбаума |
| `scale.tsx` | платформа автовесов + столбик-табло | 330×132 | цифры на табло |
| `dock.tsx` | грузовик с синей кабиной под лентой погрузки на асфальтовой площадке | 230×132 | бегущие мешки, подпись |
| `conveyor.tsx` | сегмент наклонной ленты конвейера | любой (рисуется линиями) | анимация ленты и мешков |
| `rail.tsx` | рельсы со шпалами (горизонтальный тайл) | тайл ~24px | растягивание по ширине |
| `wagon.tsx` | коричневый зерновой вагон сбоку-сверху, 4 колеса | 200×64 | номер вагона, статус-чип |
| `camera.tsx` | маркер камеры видеонаблюдения, фиолетовый | 48×48 | индикатор онлайн, живое превью |
| `parking.tsx` | легковая машинка (вид сверху) | 40×22 | разметка мест |
| `building.tsx` / `buildings.tsx` | фасады столовой/офиса/лаборатории (крыша + шапка цветная) | растягиваются | подписи, список кабинетов, мини-экран CCTV |

### Примеры готовых промптов (стиль-блок из п.1 добавляйте в конец)

- **Цистерна**: «One vertical steel grain silo tank with a conical dome top,
  brushed metal side with vertical highlight, small ladder on the right side,
  narrow tall proportions 1:3.»
- **Вагон**: «Brown railway grain hopper wagon, side-top view, open top showing
  golden grain, four dark wheels, subtle rivets.»
- **KUKA**: «Orange industrial robot arm with two joints and a gripper, on a
  dark base, KUKA-style, side view.»
- **Грузовик**: «Small delivery truck, top-side view, blue cab, light grey
  open trailer, three wheels visible.»
- **Камера**: «Small CCTV camera icon marker, violet body, lens to the right,
  soft violet halo circle behind.»

## 4. Как вставить сгенерированный SVG в ассет

1. Откройте полученный `.svg` в редакторе, скопируйте содержимое `<svg>…</svg>`
   (сами `<path>/<g>`, без обёртки `<svg>`).
2. В файле ассета найдите scale-группу с графикой (комментарий в шапке файла
   говорит, где она) и замените старые фигуры вставленными.
3. Сверьте `viewBox` файла с константами `ART_W`/`ART_H` ассета — при
   несовпадении просто пропишите новые значения, масштабирование посчитается само.
4. Цвета приведите к палитре из `palette.ts` (или обновите палитру целиком).
5. Не удаляйте `clipPath`, `<text>` и элементы с `animate*` — это живые данные
   и анимации, которые рисует код.
6. Проверьте в превью: `/warehouse/map`, наведите курсор — тултипы и превью
   камер должны работать как раньше.
