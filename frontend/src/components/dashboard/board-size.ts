/**
 * Размеры сводки «Главной». На ТВ её читают с 3–5 метров, поэтому шрифт там
 * задан от ширины экрана: на 1920 px подпись ≈ 26 px, число ≈ 80 px.
 */
export const BOARD_SIZE = {
  page: {
    title: "text-sm font-semibold",
    label: "text-xs",
    value: "text-3xl",
    unit: "text-sm",
    note: "text-xs",
    row: "text-sm",
    rowGap: "py-2.5",
    pad: "p-5",
  },
  tv: {
    title: "text-[1.45vw] font-semibold",
    label: "text-[1.3vw]",
    value: "text-[4.2vw] leading-none",
    unit: "text-[1.4vw]",
    note: "text-[1.1vw]",
    row: "text-[1.55vw] leading-tight",
    rowGap: "py-[0.4vw]",
    pad: "p-[1.3vw]",
  },
} as const;

export type BoardSize = keyof typeof BOARD_SIZE;
