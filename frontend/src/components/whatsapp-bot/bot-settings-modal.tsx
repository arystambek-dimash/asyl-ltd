"use client";
import { useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { PhoneInput } from "@/components/ui/phone-input";
import { api, apiError } from "@/lib/api";
import { cn, PHONE_INPUT_TEXT } from "@/lib/utils";
import { parseIdList, WHATSAPP_BOT_API, type WhatsAppBotSettings, type WhatsAppBotStatus } from "@/lib/whatsapp-bot";

const TEXTAREA =
  "w-full resize-y rounded-md border bg-[var(--background)] px-3 py-2 font-mono leading-snug outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)]/15";

/**
 * Настройки бота (администратор): включить, какие чаты и отправители
 * разрешены, суммы в ответе, окно дублей (± дней от даты отчёта), допуск цены
 * и кому уходит «Отправить отчёт» из истории грузчика (имя и номер WhatsApp).
 * Ответ PUT — вся шапка журнала: экран применяет его, а не перечитывает
 * опрашиваемый статус.
 */
export function BotSettingsModal({
  settings,
  onClose,
  onSaved,
}: {
  settings: WhatsAppBotSettings;
  onClose: () => void;
  onSaved: (status: WhatsAppBotStatus) => void;
}) {
  const [enabled, setEnabled] = useState(settings.enabled);
  const [chats, setChats] = useState(settings.allowed_chat_ids.join("\n"));
  const [senders, setSenders] = useState(settings.allowed_sender_ids.join("\n"));
  const [showAmounts, setShowAmounts] = useState(settings.show_amounts_in_reply);
  const [windowDays, setWindowDays] = useState(String(settings.duplicate_window_days));
  const [tolerance, setTolerance] = useState(String(Number(settings.price_tolerance_pct)));
  const [reportName, setReportName] = useState(settings.report_recipient_name);
  // Сервер хранит цифры с кодом страны; поле показывает номер по маске страны.
  const [reportPhone, setReportPhone] = useState(
    settings.report_recipient_phone ? `+${settings.report_recipient_phone}` : "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const chatIds = parseIdList(chats);
  const chatNames = new Map(settings.seen_chats.map((chat) => [chat.id, chat.name]));
  const suggestions = settings.seen_chats.filter((chat) => !chatIds.includes(chat.id));

  async function save() {
    setBusy(true);
    setError("");
    try {
      const { data } = await api.put<WhatsAppBotStatus>(`${WHATSAPP_BOT_API}/settings/`, {
        enabled,
        allowed_chat_ids: chatIds,
        allowed_sender_ids: parseIdList(senders),
        show_amounts_in_reply: showAmounts,
        duplicate_window_days: Number(windowDays),
        price_tolerance_pct: tolerance.replace(",", "."),
        report_recipient_name: reportName,
        report_recipient_phone: reportPhone,
      });
      onSaved(data);
    } catch (cause) {
      setError(apiError(cause) || "Не удалось сохранить настройки");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      variant="sheet"
      className="max-w-xl"
      eyebrow="WhatsApp-бот"
      title="Настройки бота"
      description="Бот проводит отчёты только из разрешённых чатов и только от разрешённых отправителей."
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            Отмена
          </Button>
          <Button disabled={busy} onClick={() => void save()}>
            {busy ? "Сохраняем…" : "Сохранить"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {error && <ErrorAlert message={error} />}
        <label className="flex cursor-pointer items-start gap-3 rounded-xl border p-3 text-sm">
          <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
          <span>
            <span className="font-medium">Бот проводит отчёты</span>
            <span className="block text-[12px] text-[var(--muted-foreground)]">
              Выключен — сообщения ждут у Green-API до суток и проводятся, когда бот снова включат.
            </span>
          </span>
        </label>

        <Field
          label="Чаты"
          htmlFor="bot-chats"
          hint="Группа «Отгрузка вагонов» (…@g.us), по одной в строке. Нет идентификатора — включите бота и напишите в группу: она появится ниже, а её сообщения до выбора — в «Пропущено»."
        >
          <textarea
            id="bot-chats"
            rows={2}
            spellCheck={false}
            value={chats}
            onChange={(event) => setChats(event.target.value)}
            className={cn(TEXTAREA, PHONE_INPUT_TEXT)}
          />
        </Field>
        {chatIds.some((id) => chatNames.get(id)) && (
          <ul className="-mt-2 flex flex-col gap-0.5 text-[12px] text-[var(--muted-foreground)]">
            {chatIds.map((id) => (
              <li key={id}>
                <span className="font-mono">{id}</span> — {chatNames.get(id) || "чат ещё не писал боту"}
              </li>
            ))}
          </ul>
        )}
        {suggestions.length > 0 && (
          <div className="-mt-1 flex flex-col gap-1.5">
            <span className="text-[12px] text-[var(--muted-foreground)]">Недавно писали боту:</span>
            <div className="flex flex-wrap gap-1.5">
              {suggestions.map((chat) => (
                <Button
                  key={chat.id}
                  variant="outline"
                  size="sm"
                  onClick={() => setChats((current) => [...parseIdList(current), chat.id].join("\n"))}
                >
                  <Plus /> {chat.name || chat.id}
                </Button>
              ))}
            </div>
          </div>
        )}

        <Field
          label="Отправители"
          htmlFor="bot-senders"
          hint="Кто присылает отчёты: номер телефона (+998 90 111 22 33) или идентификатор, по одному в строке."
        >
          <textarea
            id="bot-senders"
            rows={2}
            spellCheck={false}
            value={senders}
            onChange={(event) => setSenders(event.target.value)}
            className={cn(TEXTAREA, PHONE_INPUT_TEXT)}
          />
        </Field>

        <label className="flex cursor-pointer items-center gap-3 rounded-xl border p-3 text-sm">
          <input type="checkbox" checked={showAmounts} onChange={(event) => setShowAmounts(event.target.checked)} />
          Показывать сумму заказа в ответе в чат
        </label>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            label="Дубли вагонов, ± дней"
            htmlFor="bot-window"
            hint="Вагон уже отгружен в пределах ± стольких дней от даты отчёта — на проверку."
          >
            <Input
              id="bot-window"
              type="number"
              inputMode="numeric"
              min={1}
              max={60}
              value={windowDays}
              onChange={(event) => setWindowDays(event.target.value)}
              className={PHONE_INPUT_TEXT}
            />
          </Field>
          <Field
            label="Допуск цены, %"
            htmlFor="bot-tolerance"
            hint="Дальше от прошлого вагонного заказа — на проверку."
          >
            <Input
              id="bot-tolerance"
              inputMode="decimal"
              value={tolerance}
              onChange={(event) => setTolerance(event.target.value)}
              className={PHONE_INPUT_TEXT}
            />
          </Field>
        </div>

        <section aria-label="Отчёт о вагонах" className="flex flex-col gap-3 rounded-xl border p-3">
          <div className="text-sm">
            <span className="font-medium">Кому «Отправить отчёт»</span>
            <span className="block text-[12px] text-[var(--muted-foreground)]">
              Кнопка в истории грузчика (вкладка «Вагоны»). Без номера бот пишет в первую разрешённую группу, а когда
              бот выключен, WhatsApp откроет выбор чата.
            </span>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Имя" htmlFor="bot-report-name">
              <Input
                id="bot-report-name"
                maxLength={60}
                value={reportName}
                onChange={(event) => setReportName(event.target.value)}
                className={PHONE_INPUT_TEXT}
              />
            </Field>
            <Field label="Номер WhatsApp" htmlFor="bot-report-phone">
              <PhoneInput id="bot-report-phone" value={reportPhone} onChange={setReportPhone} />
            </Field>
          </div>
        </section>
      </div>
    </Modal>
  );
}
