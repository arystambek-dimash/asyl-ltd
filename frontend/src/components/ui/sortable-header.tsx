import * as React from "react";
import { ArrowUp, ArrowDown, ArrowUpDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { TH } from "./table";

export type SortDir = "asc" | "desc";

export function SortableHeader({
  label,
  sortKey,
  activeKey,
  dir,
  onClick,
  align,
}: {
  label: string;
  sortKey: string;
  activeKey: string;
  dir: SortDir;
  onClick: (k: string) => void;
  align?: "right";
}) {
  const isActive = activeKey === sortKey;
  return (
    <TH
      aria-sort={isActive ? (dir === "asc" ? "ascending" : "descending") : "none"}
      className={cn(align === "right" && "text-right")}
    >
      <button
        type="button"
        onClick={() => onClick(sortKey)}
        className={cn(
          "-mx-2 inline-flex min-h-9 items-center gap-1 rounded-lg px-2 transition-colors hover:bg-[var(--card)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]/25",
          align === "right" && "flex-row-reverse",
          isActive && "text-[var(--foreground)] font-medium",
        )}
      >
        {label}
        {isActive ? (
          dir === "asc" ? (
            <ArrowUp className="h-3 w-3" />
          ) : (
            <ArrowDown className="h-3 w-3" />
          )
        ) : (
          <ArrowUpDown className="h-3 w-3 opacity-40" />
        )}
      </button>
    </TH>
  );
}
