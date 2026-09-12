"use client";
import { QrCode } from "lucide-react";

/** Круглая кнопка POS по центру снизу — как QR-таб в Kaspi. */
export function PosFab({ onClick }: { onClick: () => void }) {
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-30 flex justify-center pb-[calc(1rem+env(safe-area-inset-bottom))]">
      <button
        type="button"
        onClick={onClick}
        aria-label="Открыть POS"
        className="pointer-events-auto flex size-16 flex-col items-center justify-center gap-0.5 rounded-full bg-[var(--primary)] text-[var(--primary-foreground)] shadow-lg transition-colors hover:bg-[var(--primary)]/90 focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/50"
      >
        <QrCode className="size-6" aria-hidden />
        <span className="text-[10px] font-semibold">POS</span>
      </button>
    </div>
  );
}
