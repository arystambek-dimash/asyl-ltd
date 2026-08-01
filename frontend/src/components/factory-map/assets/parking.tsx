import { MAP_PALETTE as P } from "../palette";

/**
 * Парковка сотрудников: растягивается на бокс зоны, число мест — от ширины.
 * Замена графики: перерисуйте содержимое <g>. Промпты: ../SVG_PROMPTS.md
 */
const CAR_COLORS = ["#7aa2e3", "#a3b8cc", "#d4a3a3", "#9cc3a5"];

export function ParkingAsset({ width, height, name }: { width: number; height: number; name: string }) {
  const stalls = Math.max(2, Math.floor(width / 58));
  const stallWidth = width / stalls;

  return (
    <g>
      <text x={width / 2} y="-9" textAnchor="middle" fontSize="12" fontWeight="600" fill={P.textMuted}>
        {name}
      </text>
      <rect width={width} height={height} rx="12" fill={P.asphalt} opacity=".85" />
      {Array.from({ length: stalls - 1 }).map((_, index) => (
        <line
          key={index}
          x1={(index + 1) * stallWidth}
          y1="10"
          x2={(index + 1) * stallWidth}
          y2={height - 10}
          stroke="#ffffff"
          strokeOpacity=".6"
          strokeWidth="2"
        />
      ))}
      {Array.from({ length: stalls }).map((_, index) => {
        if (index % 3 === 1) return null;
        const carWidth = Math.min(40, stallWidth - 16);
        const carHeight = Math.min(22, height * 0.28);
        return (
          <g
            key={index}
            transform={`translate(${index * stallWidth + (stallWidth - carWidth) / 2} ${index % 2 ? height - carHeight - 14 : 14})`}
          >
            <rect width={carWidth} height={carHeight} rx="6" fill={CAR_COLORS[index % CAR_COLORS.length]} />
            <rect x={carWidth * 0.2} y="4" width={carWidth * 0.6} height={carHeight - 8} rx="4" fill="#ffffff88" />
          </g>
        );
      })}
    </g>
  );
}
