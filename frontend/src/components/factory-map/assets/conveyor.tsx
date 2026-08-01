import { MAP_PALETTE as P } from "../palette";

/**
 * Наклонный конвейер: лента с бегущими мешками из нижнего левого угла бокса
 * в правый верхний. Геометрия считается от размера зоны — растяните бокс
 * между складом и ж/д веткой. Промпты: ../SVG_PROMPTS.md
 */
export function ConveyorAsset({ width, height }: { width: number; height: number }) {
  const x1 = 14;
  const y1 = height - 12;
  const x2 = width - 14;
  const y2 = 12;
  const path = `M ${x1} ${y1} L ${x2} ${y2}`;

  return (
    <g>
      <path d={path} stroke="#9aa5b1" strokeWidth="26" strokeLinecap="round" fill="none" />
      <path d={path} stroke="#39424e" strokeWidth="18" strokeLinecap="round" fill="none" />
      <path d={path} stroke="#cdd5de" strokeWidth="18" strokeLinecap="round" fill="none" strokeDasharray="6 14" opacity=".8">
        <animate attributeName="stroke-dashoffset" from="0" to="-100" dur="2.2s" repeatCount="indefinite" />
      </path>
      {[0, 1.1, 2.2].map((begin) => (
        <rect key={begin} x="-9" y="-7" width="18" height="14" rx="5" fill={P.bag} stroke={P.bagStroke}>
          <animateMotion dur="3.2s" begin={`${begin}s`} repeatCount="indefinite" rotate="auto" path={path} />
        </rect>
      ))}
    </g>
  );
}
