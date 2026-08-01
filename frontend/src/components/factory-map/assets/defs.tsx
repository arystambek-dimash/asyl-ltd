import { DEFS, MAP_PALETTE as P } from "../palette";

/**
 * Общие градиенты и фильтры сцены. Подключается один раз в <svg> карты.
 * Ассеты ссылаются на них через url(#fm-…) — см. DEFS в palette.ts.
 */
export function FactoryMapDefs() {
  return (
    <defs>
      <linearGradient id={DEFS.siloBody} x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stopColor="#dfe3e9" />
        <stop offset=".5" stopColor="#f4f6f9" />
        <stop offset="1" stopColor="#c9cfd8" />
      </linearGradient>
      <linearGradient id={DEFS.grain} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stopColor={P.grainTop} />
        <stop offset="1" stopColor={P.grainBottom} />
      </linearGradient>
      <linearGradient id={DEFS.roof} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stopColor="#f3f5f8" />
        <stop offset="1" stopColor="#dde1e7" />
      </linearGradient>
      <linearGradient id={DEFS.mill} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stopColor="#e9f5ee" />
        <stop offset="1" stopColor="#d3e9dc" />
      </linearGradient>
      <filter id={DEFS.soft} x="-20%" y="-20%" width="140%" height="140%">
        <feDropShadow dx="0" dy="4" stdDeviation="6" floodColor="#10182833" />
      </filter>
    </defs>
  );
}
