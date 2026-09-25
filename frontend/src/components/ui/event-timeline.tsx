import type { ReactNode } from "react";
import { cn, formatDateTime } from "@/lib/utils";

/** Лента истории: вертикальная линия слева, точки событий на ней. */
export function EventTimeline({ children }: { children: ReactNode }) {
  return (
    <div className="relative space-y-3 before:absolute before:bottom-2 before:left-[5px] before:top-2 before:w-px before:bg-[var(--border)]">
      {children}
    </div>
  );
}

/** Событие ленты; `highlighted` — зелёная точка у ключевого (последнего или свежего) события. */
export function EventTimelineItem({
  title,
  at,
  userName,
  highlighted = false,
}: {
  title: ReactNode;
  at: string;
  userName?: string | null;
  highlighted?: boolean;
}) {
  return (
    <div className="relative flex gap-3 text-xs">
      <span
        className={cn(
          "relative z-10 mt-1 size-2.5 shrink-0 rounded-full ring-4 ring-[var(--card)]",
          highlighted ? "bg-[var(--success)]" : "bg-[var(--muted-foreground)]/45",
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="font-medium">{title}</div>
        <div className="mt-0.5 text-[10px] text-[var(--muted-foreground)]">
          {formatDateTime(at)}
          {userName ? ` · ${userName}` : ""}
        </div>
      </div>
    </div>
  );
}
