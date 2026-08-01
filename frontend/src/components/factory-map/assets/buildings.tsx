import { MAP_PALETTE as P } from "../palette";
import { BuildingFrame } from "./building";

/**
 * Простые постройки на базе BuildingFrame: столовая, офис, лаборатория,
 * служебный участок. Замена графики: см. building.tsx. Промпты: ../SVG_PROMPTS.md
 */

export function CanteenAsset({
  width,
  height,
  name,
  note,
}: {
  width: number;
  height: number;
  name: string;
  note: string;
}) {
  return (
    <BuildingFrame width={width} height={height} name={name} headerFill="#f4d9b8" headerText="#8a5a24">
      {note && height >= 96 && (
        <text x={width / 2} y={height - 16} textAnchor="middle" fontSize="10.5" fill={P.textMuted}>
          {note.length > Math.floor(width / 5.6) ? `${note.slice(0, Math.floor(width / 5.6) - 1)}…` : note}
        </text>
      )}
    </BuildingFrame>
  );
}

export function OfficeAsset({
  width,
  height,
  name,
  note,
}: {
  width: number;
  height: number;
  name: string;
  note: string;
}) {
  const bullets = note
    .split("·")
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 4);
  const screenWidth = 52;
  const showScreen = width >= 170 && height >= 110;

  return (
    <BuildingFrame width={width} height={height} name={name} headerFill="#c7d8f4" headerText="#274b82" windows={0}>
      <g fontSize="10.5" fill={P.textMuted}>
        {bullets.map((item, index) => (
          <text key={item} x="18" y={46 + index * 20}>
            · {item.length > 24 ? `${item.slice(0, 23)}…` : item}
          </text>
        ))}
      </g>
      {showScreen && (
        <g transform={`translate(${width - screenWidth - 16} 42)`}>
          <rect width={screenWidth} height="36" rx="5" fill="#101418" />
          <g fill="#38506b">
            <rect x="4" y="4" width="21" height="13" rx="2" />
            <rect x="27" y="4" width="21" height="13" rx="2" />
            <rect x="4" y="19" width="21" height="13" rx="2" />
            <rect x="27" y="19" width="21" height="13" rx="2" />
          </g>
          <circle cx="47" cy="8" r="2" fill="#5df08d">
            <animate attributeName="opacity" values="1;.2;1" dur="1.4s" repeatCount="indefinite" />
          </circle>
        </g>
      )}
    </BuildingFrame>
  );
}

export function LabAsset({ width, height, name, note }: { width: number; height: number; name: string; note: string }) {
  return (
    <BuildingFrame width={width} height={height} name={name} headerFill="#e2d7f5" headerText="#5b3f82" windows={2}>
      {note && height >= 96 && (
        <text x={width / 2} y={height - 16} textAnchor="middle" fontSize="10.5" fill={P.textMuted}>
          {note.length > Math.floor(width / 5.6) ? `${note.slice(0, Math.floor(width / 5.6) - 1)}…` : note}
        </text>
      )}
    </BuildingFrame>
  );
}

export function UtilityAsset({
  width,
  height,
  name,
  note,
}: {
  width: number;
  height: number;
  name: string;
  note: string;
}) {
  return (
    <BuildingFrame width={width} height={height} name={name} headerFill="#e2e5ea" headerText="#4b5563" windows={2}>
      {note && height >= 96 && (
        <text x={width / 2} y={height - 16} textAnchor="middle" fontSize="10.5" fill={P.textMuted}>
          {note.length > Math.floor(width / 5.6) ? `${note.slice(0, Math.floor(width / 5.6) - 1)}…` : note}
        </text>
      )}
    </BuildingFrame>
  );
}
