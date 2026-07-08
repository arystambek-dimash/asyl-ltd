"use client";
import { useRef } from "react";
import { cn } from "@/lib/utils";

/*
  Казахстанский госномер: 123 ABC 02
  — 3 цифры (digits), 3 буквы (letters, лат.), 2 цифры региона (region).
  value хранится как единая строка "123ABC02"; компонент сам разбивает на части.
*/

function parse(value: string) {
  const v = (value || "").toUpperCase().replace(/[^0-9A-Z]/g, "");
  // Разбор: первые до 3 цифр, затем до 3 букв, затем до 2 цифр.
  const m = v.match(/^(\d{0,3})([A-Z]{0,3})(\d{0,2})$/);
  if (m) return { digits: m[1], letters: m[2], region: m[3] };
  // мягкий разбор, если что-то не по порядку
  const digits = v.replace(/[^0-9]/g, "").slice(0, 3);
  const letters = v.replace(/[^A-Z]/g, "").slice(0, 3);
  const region = v.replace(/[^0-9]/g, "").slice(3, 5);
  return { digits, letters, region };
}

export function LicensePlateInput({
  value, onChange,
}: {
  value: string;
  onChange: (next: string) => void;
}) {
  const { digits, letters, region } = parse(value);
  const lettersRef = useRef<HTMLInputElement>(null);
  const regionRef = useRef<HTMLInputElement>(null);
  const digitsRef = useRef<HTMLInputElement>(null);

  function emit(d: string, l: string, r: string) {
    onChange(`${d}${l}${r}`);
  }

  return (
    <div className="flex items-stretch overflow-hidden rounded-xl border bg-[var(--background)] shadow-xs focus-within:ring-[3px] focus-within:ring-[var(--ring)]/40">
      {/* KZ + флаг */}
      <div className="flex w-14 shrink-0 flex-col items-center justify-center gap-0.5 border-r bg-[var(--secondary)]/50 px-2 py-2">
        <span className="text-base leading-none">🇰🇿</span>
        <span className="text-[11px] font-bold leading-none">KZ</span>
      </div>

      {/* цифры */}
      <input
        ref={digitsRef}
        value={digits}
        inputMode="numeric"
        placeholder="123"
        aria-label="Цифры"
        className="w-0 flex-[1.2] bg-transparent px-2 py-3 text-center text-xl font-bold tracking-wide outline-none placeholder:font-bold placeholder:text-[var(--muted-foreground)]/35"
        onChange={(e) => {
          const d = e.target.value.replace(/\D/g, "").slice(0, 3);
          emit(d, letters, region);
          if (d.length === 3) lettersRef.current?.focus();
        }}
      />

      {/* буквы */}
      <input
        ref={lettersRef}
        value={letters}
        placeholder="ABC"
        aria-label="Буквы"
        className="w-0 flex-1 bg-transparent px-2 py-3 text-center text-xl font-bold tracking-wide outline-none placeholder:font-bold placeholder:text-[var(--muted-foreground)]/35"
        onChange={(e) => {
          const l = e.target.value.toUpperCase().replace(/[^A-Z]/g, "").slice(0, 3);
          emit(digits, l, region);
          if (l.length === 3) regionRef.current?.focus();
        }}
        onKeyDown={(e) => {
          if (e.key === "Backspace" && !letters) digitsRef.current?.focus();
        }}
      />

      {/* регион */}
      <div className="flex items-center border-l">
        <input
          ref={regionRef}
          value={region}
          inputMode="numeric"
          placeholder="02"
          aria-label="Регион"
          className={cn(
            "w-16 bg-transparent px-2 py-3 text-center text-xl font-bold outline-none",
            "placeholder:font-bold placeholder:text-[var(--muted-foreground)]/35"
          )}
          onChange={(e) => {
            const r = e.target.value.replace(/\D/g, "").slice(0, 2);
            emit(digits, letters, r);
          }}
          onKeyDown={(e) => {
            if (e.key === "Backspace" && !region) lettersRef.current?.focus();
          }}
        />
      </div>
    </div>
  );
}

// Форматирование для отображения: "123ABC02" → "123 ABC 02"
export function formatPlate(value: string): string {
  const { digits, letters, region } = parse(value);
  return [digits, letters, region].filter(Boolean).join(" ");
}

/** Госномер как знак: белая табличка с синей полосой KZ. Для очередей и шапок. */
export function PlateBadge({ value, size = "md", className }: {
  value: string;
  size?: "md" | "lg";
  className?: string;
}) {
  const { digits, letters, region } = parse(value);
  if (!digits && !letters) {
    return <span className={cn("font-semibold tabular-nums", className)}>—</span>;
  }
  const lg = size === "lg";
  return (
    <span className={cn(
      "inline-flex select-none items-stretch overflow-hidden rounded-md border-2 border-neutral-800 bg-white font-bold tabular-nums text-neutral-900 shadow-sm",
      className
    )}>
      <span className={cn(
        "flex flex-col items-center justify-center bg-[#0057b8] px-1 leading-none text-white",
        lg ? "text-[10px]" : "text-[8px]"
      )}>
        KZ
      </span>
      <span className={cn("flex items-center tracking-wider", lg ? "px-3 text-2xl" : "px-2 text-sm")}>
        {digits}&nbsp;{letters}
      </span>
      <span className={cn(
        "flex items-center border-l-2 border-neutral-300",
        lg ? "px-2 text-2xl" : "px-1.5 text-sm"
      )}>
        {region}
      </span>
    </span>
  );
}
