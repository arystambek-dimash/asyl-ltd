"use client";
import { useEffect, useRef, useState } from "react";
import { History, Sparkles } from "lucide-react";
import { ChatList } from "@/components/assistant/chat-list";
import { Composer } from "@/components/assistant/composer";
import { AssistantMessageView, WorkingCard } from "@/components/assistant/message-view";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { FormError } from "@/components/ui/data-state";
import { api, apiError } from "@/lib/api";
import {
  ASSISTANT_CHATS_URL,
  AskFailed,
  askAssistant,
  upsertStep,
  type AssistantChat,
  type AssistantChatDetail,
  type AssistantMessage,
  type DataMap,
  type TraceStep,
} from "@/lib/assistant";
import { useApi } from "@/lib/use-api";
import { useConfirmAction } from "@/lib/use-confirm-action";
import { cn } from "@/lib/utils";

const SUGGESTIONS = [
  "Какой долг у клиентов на сегодня? Топ-10 по сумме",
  "Сколько мешков отгрузили за эту неделю по товарам?",
  "Какие заказы ждут отгрузки?",
  "Сколько денег приняли вчера и какими способами?",
  "Какие остатки на складах?",
];

type Pending = { question: string; saved: boolean; steps: TraceStep[]; map: DataMap; startedAt: number };

const EMPTY_MAP: DataMap = { tables: [], links: [] };

function upsertChat(chats: AssistantChat[] | null, chat: AssistantChat): AssistantChat[] {
  return [chat, ...(chats ?? []).filter((item) => item.id !== chat.id)];
}

function AssistantPageInner() {
  const chats = useApi<AssistantChat[]>(ASSISTANT_CHATS_URL);
  const [chatId, setChatId] = useState<number | null>(null);
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState("");
  // Только что пришедший ответ: его текст «печатается», старые — сразу целиком.
  const [freshId, setFreshId] = useState<number | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const askController = useRef<AbortController | null>(null);
  const openRequest = useRef(0);
  const end = useRef<HTMLDivElement>(null);

  const deleteAction = useConfirmAction<AssistantChat>(async (chat) => {
    await api.delete(`${ASSISTANT_CHATS_URL}${chat.id}/`);
    chats.setData((list) => (list ?? []).filter((item) => item.id !== chat.id));
    if (chat.id === chatId) startNewChat();
  });

  useEffect(() => () => askController.current?.abort(), []);
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end", behavior: "smooth" });
  }, [messages.length, pending?.steps.length, pending?.map.tables.length, pending?.saved]);

  function startNewChat() {
    openRequest.current += 1;
    setChatId(null);
    setMessages([]);
    setError("");
    setHistoryOpen(false);
  }

  async function openChat(chat: AssistantChat) {
    const request = ++openRequest.current;
    setChatId(chat.id);
    setMessages([]);
    setError("");
    setHistoryOpen(false);
    try {
      const { data } = await api.get<AssistantChatDetail>(`${ASSISTANT_CHATS_URL}${chat.id}/`);
      if (request === openRequest.current) setMessages(data.messages);
    } catch (e) {
      if (request === openRequest.current) setError(apiError(e) || "Не удалось открыть разговор.");
    }
  }

  async function ask(question: string) {
    const controller = new AbortController();
    askController.current = controller;
    openRequest.current += 1;
    setError("");
    setPending({ question, saved: false, steps: [], map: EMPTY_MAP, startedAt: Date.now() });
    try {
      await askAssistant(
        question,
        chatId,
        (event) => {
          if (event.type === "start") {
            setChatId(event.chat.id);
            setMessages((list) => [...list, event.question]);
            setPending((state) => state && { ...state, saved: true });
            chats.setData((list) => upsertChat(list, event.chat));
          } else if (event.type === "step") {
            setPending((state) => state && { ...state, steps: upsertStep(state.steps, event.step) });
          } else if (event.type === "map") {
            setPending((state) => state && { ...state, map: { tables: event.tables, links: event.links } });
          } else {
            setFreshId(event.message.id);
            setMessages((list) => [...list, event.message]);
          }
        },
        controller.signal,
      );
    } catch (e) {
      if (!controller.signal.aborted) {
        setError(
          e instanceof AskFailed
            ? e.message
            : "Связь прервалась до ответа. Ответ мог сохраниться — откройте разговор в истории.",
        );
      }
    } finally {
      if (askController.current === controller) askController.current = null;
      setPending(null);
    }
  }

  const busy = pending !== null;
  const empty = messages.length === 0 && !pending;

  return (
    <AppShell
      section="Обзор"
      title="ИИ-ассистент"
      description="Отвечает на вопросы по данным системы. Только читает — ничего не меняет."
      actions={
        <Button variant="outline" size="sm" className="lg:hidden" onClick={() => setHistoryOpen((open) => !open)}>
          <History />
          История
        </Button>
      }
    >
      <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
        <aside className={cn("lg:block", historyOpen ? "block" : "hidden")}>
          <Card className="p-2 lg:sticky lg:top-4">
            <ChatList
              chats={chats.data ?? []}
              activeId={chatId}
              disabled={busy}
              onOpen={(chat) => void openChat(chat)}
              onNew={startNewChat}
              onDelete={deleteAction.open}
            />
            <FormError message={chats.error} className="mt-2" />
          </Card>
        </aside>

        {/* Своя прокрутка у переписки: поле вопроса всегда внизу карточки, без зазора под ним. */}
        <Card className="flex h-[calc(100dvh-200px)] min-h-[460px] flex-col overflow-hidden">
          <div className="@container min-h-0 flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
            {empty && (
              <div className="flex flex-col items-center gap-4 py-10 text-center">
                <span className="flex size-12 items-center justify-center rounded-full bg-[var(--muted)]">
                  <Sparkles className="size-6 text-[var(--muted-foreground)]" />
                </span>
                <div className="space-y-1">
                  <p className="font-medium">Спросите что угодно о работе предприятия</p>
                  <p className="text-sm text-[var(--muted-foreground)]">
                    Ассистент сам найдёт данные в базе и покажет, как посчитал.
                  </p>
                </div>
                <div className="flex max-w-2xl flex-wrap justify-center gap-2">
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      onClick={() => void ask(suggestion)}
                      className="rounded-full border border-[var(--border)] px-3 py-1.5 text-xs transition-colors hover:bg-[var(--muted)]"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((message) => (
              <AssistantMessageView key={message.id} message={message} fresh={message.id === freshId} />
            ))}
            {pending && (
              <WorkingCard
                question={pending.saved ? null : pending.question}
                steps={pending.steps}
                map={pending.map}
                startedAt={pending.startedAt}
              />
            )}
            <FormError message={error} />
            <div ref={end} />
          </div>
          <div className="border-t border-[var(--border)] bg-[var(--card)] p-3 sm:p-4">
            <Composer
              busy={busy}
              onAsk={(question) => void ask(question)}
              onStop={() => askController.current?.abort()}
            />
          </div>
        </Card>
      </div>

      <ConfirmDialog
        {...deleteAction.dialog}
        title="Удалить разговор?"
        description={deleteAction.item ? `«${deleteAction.item.title}» исчезнет из истории.` : undefined}
      />
    </AppShell>
  );
}

export default function AssistantPage() {
  return (
    <RequirePerm perm="assistant.view" title="ИИ-ассистент">
      <AssistantPageInner />
    </RequirePerm>
  );
}
