// next-env.d.ts генерится Next-ом и гитигнорится, поэтому в CI на шаге
// `npm run check` (до build) типов для импорта картинок нет — подключаем явно.
/// <reference types="next/image-types/global" />
