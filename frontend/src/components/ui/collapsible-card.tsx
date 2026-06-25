"use client";
import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";

export function CollapsibleCard({
  title,
  defaultOpen = false,
  badge,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  badge?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  return (
    <Card>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 p-6 text-left"
        aria-expanded={open}
      >
        <span className="flex items-center gap-2 font-semibold leading-none tracking-tight">
          {title}
          {badge}
        </span>
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-[var(--muted-foreground)] transition-transform",
            open && "rotate-180"
          )}
        />
      </button>
      {open && <div className="flex flex-col gap-3 px-6 pb-6">{children}</div>}
    </Card>
  );
}
