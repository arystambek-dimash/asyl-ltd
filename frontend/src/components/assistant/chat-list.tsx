"use client";
import { MessageSquarePlus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { AssistantChat } from "@/lib/assistant";
import { cn, formatDateTime } from "@/lib/utils";

/** История разговоров сотрудника: новый разговор, открыть, удалить. */
export function ChatList({
  chats,
  activeId,
  disabled,
  onOpen,
  onNew,
  onDelete,
}: {
  chats: AssistantChat[];
  activeId: number | null;
  disabled: boolean;
  onOpen: (chat: AssistantChat) => void;
  onNew: () => void;
  onDelete: (chat: AssistantChat) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <Button variant="outline" className="w-full justify-start" onClick={onNew} disabled={disabled}>
        <MessageSquarePlus />
        Новый разговор
      </Button>
      {chats.length === 0 ? (
        <p className="px-2 py-3 text-xs text-[var(--muted-foreground)]">Здесь появятся ваши разговоры.</p>
      ) : (
        <ul className="space-y-0.5">
          {chats.map((chat) => (
            <li key={chat.id} className="group relative">
              <button
                type="button"
                onClick={() => onOpen(chat)}
                disabled={disabled}
                aria-current={chat.id === activeId ? "true" : undefined}
                className={cn(
                  "w-full rounded-lg px-3 py-2 pr-9 text-left transition-colors hover:bg-[var(--muted)] disabled:opacity-60",
                  chat.id === activeId && "bg-[var(--muted)]",
                )}
              >
                <span className="block truncate text-sm">{chat.title}</span>
                <span className="block text-[11px] text-[var(--muted-foreground)]">
                  {formatDateTime(chat.updated_at)}
                </span>
              </button>
              <button
                type="button"
                onClick={() => onDelete(chat)}
                disabled={disabled}
                aria-label={`Удалить разговор «${chat.title}»`}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-[var(--muted-foreground)] transition hover:bg-[var(--background)] hover:text-[var(--destructive)] focus-visible:opacity-100 disabled:hidden lg:opacity-0 lg:group-hover:opacity-100"
              >
                <Trash2 className="size-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
