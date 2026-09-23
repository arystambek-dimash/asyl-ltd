"use client";
import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { ChevronDown, EyeOff, MessageCircle, PackageCheck, Search, Settings, Sparkles } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { IssueList, RailReportSheet } from "@/components/loader/rail-report-sheet";
import { RequirePerm } from "@/components/require-perm";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { LoadMore } from "@/components/ui/load-more";
import { Tabs } from "@/components/ui/tabs";
import { BotSettingsModal } from "@/components/whatsapp-bot/bot-settings-modal";
import { api, apiError } from "@/lib/api";
import { withBack } from "@/lib/navigation";
import { useApi } from "@/lib/use-api";
import { useDebounced } from "@/lib/use-debounced";
import { usePagedApi } from "@/lib/use-paged-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { cn, formatDateTime, PHONE_INPUT_TEXT } from "@/lib/utils";
import {
  botHealth,
  canConduct,
  canIgnore,
  inTab,
  INSTANCE_STATES,
  MESSAGE_KIND,
  MESSAGE_STATUS,
  messageApi,
  messageSummary,
  textToConduct,
  WHATSAPP_BOT_API,
  type BotMessage,
  type BotTab,
  type WhatsAppBotStatus,
} from "@/lib/whatsapp-bot";

const PAGE = "/management/whatsapp-bot";
const STATUS_URL = `${WHATSAPP_BOT_API}/status/`;
const POLL_MS = 15_000;
const TABS: { key: BotTab; label: string }[] = [
  { key: "review", label: "На проверке" },
  { key: "applied", label: "Проведено" },
  { key: "ignored", label: "Пропущено" },
  { key: "all", label: "Все" },
];

export default function WhatsAppBotPage() {
  return (
    <RequirePerm perm="bots.view" title="WhatsApp-бот">
      <WhatsAppBotJournal />
    </RequirePerm>
  );
}

function WhatsAppBotJournal() {
  const status = useApi<WhatsAppBotStatus>(STATUS_URL);
  const [tab, setTab] = useState<BotTab>("review");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search);
  const url = useMemo(() => {
    const query = new URLSearchParams({ status: tab });
    if (debouncedSearch) query.set("search", debouncedSearch);
    return `${WHATSAPP_BOT_API}/messages/?${query}`;
  }, [tab, debouncedSearch]);
  const messages = usePagedApi<BotMessage>(url, 50);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [conducting, setConducting] = useState<BotMessage | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [rowError, setRowError] = useState<{ id: number; message: string } | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const setStatus = status.setData;
  const refreshStatus = useCallback(async () => {
    try {
      const { data } = await api.get<WhatsAppBotStatus>(STATUS_URL);
      setStatus(data);
    } catch {
      // Тихий опрос: последняя шапка остаётся на экране.
    }
    setNow(Date.now());
  }, [setStatus]);

  // Бот работает сам: шапка и список обновляются тихо, пока человек ничего не решает.
  useVisiblePolling(
    async () => {
      await Promise.all([refreshStatus(), messages.refresh()]);
    },
    POLL_MS,
    conducting === null && !settingsOpen && busyId === null,
  );

  const applyRow = messages.applyItems;
  /** Ответ «Провести»/«Игнорировать» — строка журнала: применяем её, счётчики перечитываем. */
  const applyDecision = useCallback(
    (row: BotMessage) => {
      applyRow((items) =>
        inTab(row, tab)
          ? items.map((item) => (item.id === row.id ? row : item))
          : items.filter((item) => item.id !== row.id),
      );
      void refreshStatus();
    },
    [applyRow, refreshStatus, tab],
  );

  async function ignore(message: BotMessage) {
    setBusyId(message.id);
    setRowError(null);
    try {
      const { data } = await api.post<BotMessage>(`${messageApi(message.id)}/ignore/`);
      applyDecision(data);
    } catch (cause) {
      setRowError({ id: message.id, message: apiError(cause) || "Не удалось пропустить сообщение" });
    } finally {
      setBusyId(null);
    }
  }

  const data = status.data;
  const canManage = Boolean(data?.can_manage);
  return (
    <AppShell
      title="WhatsApp-бот"
      section="Управление"
      description="Отчёты о вагонах из группы в WhatsApp: всё, что сошлось, бот проводит сам — остальное ждёт здесь."
      actions={
        data?.can_configure && (
          <Button variant="outline" size="sm" onClick={() => setSettingsOpen(true)}>
            <Settings /> Настройки
          </Button>
        )
      }
    >
      <div className="flex flex-col gap-4">
        {data ? (
          <StatusCard status={data} now={now} />
        ) : (
          <DataGate loading={status.loading} error={status.error} onRetry={status.reload} />
        )}

        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <Tabs
            label="Сообщения"
            tabs={TABS.map((item) => ({ ...item, count: item.key === "review" ? data?.counts.review : undefined }))}
            active={tab}
            onChange={(key) => {
              setTab(key as BotTab);
              setExpanded(null);
            }}
            className="overflow-x-auto"
          />
          <div className="relative sm:w-72">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
            <Input
              aria-label="Поиск по сообщениям"
              placeholder="Текст, отправитель, № заказа"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className={cn("pl-9", PHONE_INPUT_TEXT)}
            />
          </div>
        </div>

        {messages.error && <ErrorAlert message={messages.error} onRetry={messages.reload} />}
        {messages.refreshError && (
          <p className="text-[12px] text-[var(--muted-foreground)]">Нет связи — показаны последние данные.</p>
        )}
        {messages.loading && messages.items.length === 0 ? (
          <p className="text-sm text-[var(--muted-foreground)]">Загрузка…</p>
        ) : messages.items.length === 0 ? (
          <Card className="flex flex-col items-center gap-2 px-6 py-10 text-center">
            <MessageCircle className="size-6 text-[var(--muted-foreground)]" />
            <p className="text-sm text-[var(--muted-foreground)]">
              {tab === "review" ? "Проверять нечего — бот всё провёл сам." : "Сообщений нет."}
            </p>
          </Card>
        ) : (
          <Card className="overflow-hidden p-0">
            <ul className="divide-y">
              {messages.items.map((message) => (
                <MessageRow
                  key={message.id}
                  message={message}
                  open={expanded === message.id}
                  onToggle={() => setExpanded((current) => (current === message.id ? null : message.id))}
                  canManage={canManage}
                  busy={busyId === message.id}
                  error={rowError?.id === message.id ? rowError.message : ""}
                  onConduct={() => setConducting(message)}
                  onIgnore={() => void ignore(message)}
                />
              ))}
            </ul>
          </Card>
        )}
        <LoadMore
          shown={messages.items.length}
          total={messages.count}
          hasMore={messages.hasMore}
          loading={messages.loadingMore}
          onClick={messages.loadMore}
        />
      </div>

      {conducting && (
        <RailReportSheet<BotMessage>
          api={messageApi(conducting.id)}
          initialText={textToConduct(conducting)}
          eyebrow={`WhatsApp · ${conducting.sender_name || conducting.sender_id}`}
          title="Провести сообщение"
          onClose={() => setConducting(null)}
          onApplied={(row) => {
            setConducting(null);
            applyDecision(row);
          }}
        />
      )}
      {settingsOpen && data && (
        <BotSettingsModal
          settings={data.settings}
          onClose={() => setSettingsOpen(false)}
          onSaved={(next) => {
            setStatus(next);
            setSettingsOpen(false);
          }}
        />
      )}
    </AppShell>
  );
}

function StatusCard({ status, now }: { status: WhatsAppBotStatus; now: number }) {
  const health = botHealth(status, now);
  const { settings } = status;
  return (
    <Card className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={health.tone} dot>
            {health.label}
          </Badge>
          <span className="min-w-0 truncate text-[13px] text-[var(--muted-foreground)]">{health.detail}</span>
        </div>
        <p className="mt-1.5 text-[13px]">
          Номер:{" "}
          <span className={cn(status.instance_state !== "authorized" && "text-[var(--warning)]")}>
            {status.instance_state
              ? (INSTANCE_STATES[status.instance_state] ?? status.instance_state)
              : "состояние неизвестно"}
          </span>
        </p>
      </div>
      <dl className="grid shrink-0 grid-cols-3 gap-4 text-[12px] sm:text-right">
        <div>
          <dt className="text-[var(--muted-foreground)]">Чатов</dt>
          <dd className="text-sm font-semibold tabular-nums">{settings.allowed_chat_ids.length}</dd>
        </div>
        <div>
          <dt className="text-[var(--muted-foreground)]">Отправителей</dt>
          <dd className="text-sm font-semibold tabular-nums">{settings.allowed_sender_ids.length}</dd>
        </div>
        <div>
          <dt className="text-[var(--muted-foreground)]">На проверке</dt>
          <dd className="text-sm font-semibold tabular-nums">{status.counts.review}</dd>
        </div>
      </dl>
    </Card>
  );
}

function MessageRow({
  message,
  open,
  onToggle,
  canManage,
  busy,
  error,
  onConduct,
  onIgnore,
}: {
  message: BotMessage;
  open: boolean;
  onToggle: () => void;
  canManage: boolean;
  busy: boolean;
  error: string;
  onConduct: () => void;
  onIgnore: () => void;
}) {
  const meta = MESSAGE_STATUS[message.status];
  const panelId = `bot-message-${message.id}`;
  return (
    <li>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
        className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-[var(--muted)]/40"
      >
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone={meta.tone}>{meta.label}</Badge>
            {message.kind !== "message" && <Badge tone="outline">{MESSAGE_KIND[message.kind]}</Badge>}
            {message.order !== null && <Badge tone="outline">Заказ №{message.order}</Badge>}
          </div>
          <p className="mt-1 truncate text-sm font-medium">{messageSummary(message)}</p>
          <p className="mt-0.5 truncate text-[12px] text-[var(--muted-foreground)]">
            {message.sender_name || message.sender_id} · {formatDateTime(message.sent_at ?? message.received_at)}
            {message.issues.length > 0 && message.status !== "applied" && ` · ${message.issues[0].message}`}
          </p>
        </div>
        <ChevronDown
          className={cn(
            "mt-1 size-4 shrink-0 text-[var(--muted-foreground)] transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <div id={panelId} className="flex flex-col gap-3 border-t bg-[var(--muted)]/20 px-4 py-3">
          {message.text ? (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border bg-[var(--background)] px-3 py-2 font-mono text-[13px] leading-snug">
              {message.text}
            </pre>
          ) : (
            <p className="text-sm text-[var(--muted-foreground)]">Текста нет — сообщение удалено отправителем.</p>
          )}
          {message.status !== "applied" && message.issues.length > 0 && (
            <IssueList tone="warning" title="Почему не проведено" issues={message.issues} />
          )}
          {message.draft && (
            <section aria-label="Черновик ИИ" className="flex flex-col gap-1.5">
              <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold">
                <Sparkles className="size-3.5" /> Черновик ИИ — проверьте перед проведением
              </span>
              <pre className="overflow-auto whitespace-pre-wrap rounded-lg border border-dashed bg-[var(--background)] px-3 py-2 font-mono text-[13px] leading-snug">
                {message.draft}
              </pre>
            </section>
          )}
          <dl className="grid gap-x-6 gap-y-1.5 text-[13px] sm:grid-cols-[auto_1fr]">
            {message.reply && (
              <>
                <dt className="text-[var(--muted-foreground)]">Ответ в чат</dt>
                <dd>
                  {message.reply}
                  <span className="block text-[12px] text-[var(--muted-foreground)]">
                    {message.reply_sent_at
                      ? `отправлен ${formatDateTime(message.reply_sent_at)}`
                      : message.reply_attempts > 0
                        ? `не отправлен (попыток: ${message.reply_attempts})`
                        : "ждёт отправки"}
                  </span>
                </dd>
              </>
            )}
            {message.resolved_by_name && message.resolved_at && (
              <>
                <dt className="text-[var(--muted-foreground)]">
                  {message.status === "ignored" ? "Пропустил" : "Провёл"}
                </dt>
                <dd>
                  {message.resolved_by_name}, {formatDateTime(message.resolved_at)}
                </dd>
              </>
            )}
            {message.original !== null && (
              <>
                <dt className="text-[var(--muted-foreground)]">Исходное</dt>
                <dd>сообщение №{message.original}</dd>
              </>
            )}
            {message.error && (
              <>
                <dt className="text-[var(--muted-foreground)]">Ошибка</dt>
                <dd className="break-words text-[var(--destructive)]">{message.error}</dd>
              </>
            )}
          </dl>
          {error && <ErrorAlert message={error} />}
          <div className="flex flex-wrap gap-2">
            {message.order !== null && (
              <Link
                href={withBack(`/orders/${message.order}`, PAGE)}
                className={buttonVariants({ variant: "outline", size: "sm" })}
              >
                Открыть заказ №{message.order}
              </Link>
            )}
            {canManage && canConduct(message) && (
              <Button size="sm" disabled={busy} onClick={onConduct}>
                <PackageCheck /> Провести…
              </Button>
            )}
            {canManage && canIgnore(message) && (
              <Button variant="ghost" size="sm" disabled={busy} onClick={onIgnore}>
                <EyeOff /> {busy ? "Пропускаем…" : "Игнорировать"}
              </Button>
            )}
          </div>
        </div>
      )}
    </li>
  );
}
