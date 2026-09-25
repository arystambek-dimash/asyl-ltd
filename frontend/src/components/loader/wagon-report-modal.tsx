"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, ClipboardCopy, LoaderCircle, MessageCircle, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Textarea } from "@/components/ui/textarea";
import { api, apiError, apiErrorCode } from "@/lib/api";
import { copyText, whatsappLink } from "@/lib/clipboard";
import {
  composeUrl,
  newSendKey,
  recipientLine,
  WAGON_REPORT_API,
  type WagonReportDelivery,
  type WagonReportDraft,
  type WagonReportScope,
  type WagonReportSent,
} from "@/lib/wagon-report";

/**
 * «Отправить отчёт» из истории вагонов: сервер составляет отчёт в формате
 * владельца (одна отгрузка или весь период экрана), текст можно поправить и
 * скопировать. «Отправить Динаре»: бот включён — сообщение встаёт в его
 * очередь; иначе открывается WhatsApp с готовым текстом (вкладка — прямо в
 * нажатии, иначе браузер счёл бы её всплывающим окном), а сервер только
 * отмечает отправку. WhatsApp уже открыт, а отметка не записалась — повтор
 * только отмечает («Отметить отправленным»), второй раз WhatsApp открывает
 * отдельная кнопка: иначе Динара получила бы отчёт дважды. Ответ отправки —
 * отметка для строк истории (onSent). Ошибки — внутри окна.
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
  // WhatsApp с этим текстом уже открыт: повтор после сбоя отметки его не открывает.
  const [linkOpened, setLinkOpened] = useState(false);
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
  const canSend = draft !== null && !empty && text.trim() !== "" && !busy && sent === null;

  async function copy() {
    setCopied((await copyText(text)) ? "ok" : "failed");
  }

  async function record(delivery: WagonReportDelivery) {
    if (!draft) return;
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post<WagonReportSent>(`${WAGON_REPORT_API}/send/`, {
        order_ids: draft.order_ids,
        text,
        delivery,
        key: sendKey.current,
      });
      setSent(data);
      onSent(data);
    } catch (cause) {
      setError(apiError(cause));
      // Бота выключили, пока окно было открыто: покажем, как отчёт уйдёт теперь.
      if (apiErrorCode(cause) === "report_bot_unavailable") void compose({ keepText: true }).catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }

  function openWhatsApp(phone: string) {
    window.open(whatsappLink(phone, text), "_blank", "noopener");
  }

  function send() {
    if (!canSend || !draft) return;
    if (draft.delivery === "link" && !linkOpened) {
      // Синхронно в нажатии: после ожидания ответа браузер заблокировал бы вкладку.
      openWhatsApp(draft.recipient.phone);
      setLinkOpened(true);
    }
    void record(draft.delivery);
  }

  const to = draft?.recipient.to ?? "";
  let sendLabel = to ? `Отправить ${to}` : "Отправить";
  if (linkOpened) sendLabel = "Отметить отправленным";
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
          {/* WhatsApp не открылся (закрыли вкладку) — открыть ещё раз, без второй отметки. */}
          {(sent?.status === "link" || (linkOpened && !sent)) && (
            <Button
              variant="outline"
              className="max-sm:grow"
              disabled={busy}
              onClick={() => openWhatsApp(sent?.recipient.phone ?? draft?.recipient.phone ?? "")}
            >
              <MessageCircle /> Открыть WhatsApp ещё раз
            </Button>
          )}
          {sent?.status !== "link" && (
            <Button className="max-sm:grow" disabled={!canSend} onClick={send}>
              {busy ? <LoaderCircle className="animate-spin" /> : sent ? <CheckCircle2 /> : <Send />}
              {sendLabel}
            </Button>
          )}
        </div>
      }
    >
      <div className="flex min-w-0 flex-col gap-3">
        {loading && <p className="text-sm text-[var(--muted-foreground)]">Составляем отчёт…</p>}
        {draft && (
          <p className="text-sm">
            <span className="text-[var(--muted-foreground)]">Кому: </span>
            <span className="font-medium">{recipientLine(draft)}</span>
            {draft.delivery === "bot" && (
              <span className="block text-[12px] text-[var(--muted-foreground)]">Отправит WhatsApp-бот.</span>
            )}
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
              readOnly={sent !== null || linkOpened}
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
            {sent.status === "link"
              ? `Открыли WhatsApp — отправьте сообщение ${sent.recipient.to}. Отгрузки отмечены как отправленные.`
              : sent.status === "queued"
                ? `В очереди — бот отправит ${sent.recipient.to} в течение минуты.`
                : `${sent.status_label}: ${sent.recipient.to}.`}
          </p>
        )}
      </div>
    </Modal>
  );
}
