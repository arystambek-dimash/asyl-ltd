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
    <TH className={cn(align === "right" && "text-right")}>
      <button
        type="button"
        onClick={() => onClick(sortKey)}
        className={cn(
          "inline-flex items-center gap-1 transition-colors hover:text-[var(--foreground)]",
          align === "right" && "flex-row-reverse",
          isActive && "text-[var(--foreground)] font-medium"
        )}
      >
        {label}
        {isActive ? (
          dir === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />
        ) : (
          <ArrowUpDown className="h-3 w-3 opacity-40" />
        )}
      </button>
    </TH>
  );
}
