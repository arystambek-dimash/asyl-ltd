"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, ClipboardCopy, LoaderCircle, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Textarea } from "@/components/ui/textarea";
import { api, apiError, apiErrorCode } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import {
  blockerText,
  composeUrl,
  newSendKey,
  recipientLabel,
  WAGON_REPORT_API,
  type WagonReportDraft,
  type WagonReportScope,
  type WagonReportSent,
} from "@/lib/wagon-report";

/**
 * «Отправить отчёт» из истории вагонов: сервер составляет отчёт в формате
 * владельца (одна отгрузка или весь период экрана), текст можно поправить и
 * скопировать. «Отправить» ставит его в очередь Telegram-бота — каждому
 * получателю из настроек бота, кто хоть раз написал боту /start. Кто не
 * писал — видно сразу, до отправки. Бот не работает или писать некому —
 * «Отправить» недоступно, окно говорит почему. Ответ отправки — отметка для
 * строк истории (onSent). Ошибки — внутри окна.
 */
export function WagonReportModal({
  scope,
  onClose,
  onSent,
}: {
  scope: WagonReportScope;
  onClose: () => void;
  onSent: (sent: WagonReportSent) => void;
}) {
  const url = composeUrl(scope);
  const [draft, setDraft] = useState<WagonReportDraft | null>(null);
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<WagonReportSent | null>(null);
  const [copied, setCopied] = useState<"" | "ok" | "failed">("");
  // Ключ нажатия: повтор после обрыва связи не отправит отчёт второй раз.
  const sendKey = useRef(newSendKey());

  const compose = useCallback(
    async ({ keepText = false } = {}) => {
      const { data } = await api.get<WagonReportDraft>(url);
      setDraft(data);
      if (!keepText) setText(data.text);
    },
    [url],
  );

  useEffect(() => {
    let active = true;
    setLoading(true);
    compose()
      .catch((cause: unknown) => {
        if (active) setError(apiError(cause));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [compose]);

  const empty = draft !== null && draft.order_ids.length === 0;
  const canSend = draft !== null && draft.can_send && !empty && text.trim() !== "" && !busy && sent === null;

  async function copy() {
    setCopied((await copyText(text)) ? "ok" : "failed");
  }

  async function send() {
    if (!canSend || !draft) return;
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post<WagonReportSent>(`${WAGON_REPORT_API}/send/`, {
        order_ids: draft.order_ids,
        text,
        key: sendKey.current,
      });
      setSent(data);
      onSent(data);
    } catch (cause) {
      setError(apiError(cause));
      // Бот или получатели изменились, пока окно было открыто: покажем, как есть сейчас.
      if (apiErrorCode(cause).startsWith("report_")) void compose({ keepText: true }).catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }

  const blocker = draft ? blockerText(draft) : "";
  const waiting = draft?.recipients.filter((recipient) => !recipient.ready) ?? [];
  let sendLabel = "Отправить";
  if (sent) sendLabel = "Отправлено";
  if (busy) sendLabel = "Отправляем…";
  return (
    <Modal
      open
      onClose={onClose}
      variant="sheet"
      className="max-w-xl"
      eyebrow={"order" in scope ? `Вагоны · заказ №${scope.order}` : "Вагоны · отчёт за период"}
      title="Отправить отчёт"
      footer={
        <div className="flex flex-1 flex-wrap items-center justify-end gap-2">
          {error && (
            <div className="basis-full">
              <ErrorAlert message={error} />
            </div>
          )}
          <Button variant="ghost" className="max-sm:grow" disabled={busy} onClick={onClose}>
            Закрыть
          </Button>
          <Button
            variant="outline"
            className="max-sm:grow"
            disabled={!draft || empty || !text.trim()}
            onClick={() => void copy()}
          >
            <ClipboardCopy /> {copied === "ok" ? "Скопировано" : "Скопировать"}
          </Button>
          <Button className="max-sm:grow" disabled={!canSend} onClick={() => void send()}>
            {busy ? <LoaderCircle className="animate-spin" /> : sent ? <CheckCircle2 /> : <Send />}
            {sendLabel}
          </Button>
        </div>
      }
    >
      <div className="flex min-w-0 flex-col gap-3">
        {loading && <p className="text-sm text-[var(--muted-foreground)]">Составляем отчёт…</p>}
        {draft && draft.recipients.length > 0 && (
          <section aria-label="Кому" className="flex flex-col gap-1.5 text-sm">
            <span className="text-[var(--muted-foreground)]">Кому (Telegram-бот):</span>
            <ul className="flex flex-col gap-1">
              {draft.recipients.map((recipient) => (
                <li
                  key={recipient.username}
                  className={cn("flex min-w-0 items-start gap-2", !recipient.ready && "text-[var(--muted-foreground)]")}
                >
                  {recipient.ready ? (
                    <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-[var(--success)]" />
                  ) : (
                    <AlertTriangle className="mt-0.5 size-4 shrink-0 text-[var(--warning)]" />
                  )}
                  <span className="min-w-0 break-words">
                    <span className={cn(recipient.ready && "font-medium")}>{recipientLabel(recipient)}</span>
                    {!recipient.ready && " — ещё не писал боту /start, не получит"}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}
        {blocker && (
          <p
            role="note"
            className="rounded-xl border border-[var(--warning)]/40 bg-[var(--warning)]/10 px-4 py-3 text-sm"
          >
            {blocker}
          </p>
        )}
        {!blocker && waiting.length > 0 && !sent && (
          <p className="text-[12px] text-[var(--muted-foreground)]">
            Попросите их открыть бота и нажать /start — тогда следующие отчёты дойдут и до них.
          </p>
        )}
        {empty && (
          <p className="rounded-xl bg-[var(--muted)]/50 px-4 py-3 text-sm text-[var(--muted-foreground)]">
            За выбранный период нет отгрузок вагонов — отправлять нечего.
          </p>
        )}
        {draft && !empty && (
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wagon-report-text">Текст отчёта</Label>
            <Textarea
              mono
              id="wagon-report-text"
              rows={Math.min(14, Math.max(5, text.split("\n").length + 1))}
              spellCheck={false}
              value={text}
              readOnly={sent !== null}
              onChange={(event) => {
                setText(event.target.value);
                setCopied("");
              }}
            />
            {copied === "failed" && (
              <p className="text-[12px] text-[var(--destructive)]">
                Браузер не дал доступ к буферу — выделите текст и скопируйте вручную.
              </p>
            )}
          </div>
        )}
        {sent && (
          <p
            role="status"
            className="flex items-start gap-2 rounded-xl border border-[var(--success)]/30 bg-[var(--success)]/10 px-4 py-3 text-sm"
          >
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-[var(--success)]" />
            {`В очереди — бот отправит в течение минуты: ${sent.deliveries.map((delivery) => delivery.to).join(", ")}.`}
          </p>
        )}
      </div>
    </Modal>
  );
}
