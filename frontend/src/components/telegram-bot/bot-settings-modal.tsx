"use client";
import { useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Textarea } from "@/components/ui/textarea";
import { api, apiError } from "@/lib/api";
import { PHONE_INPUT_TEXT } from "@/lib/utils";
import {
  normalizeUsername,
  parseUsernames,
  TELEGRAM_BOT_API,
  type TelegramBotSettings,
  type TelegramBotStatus,
} from "@/lib/telegram-bot";

/**
 * Настройки бота (администратор): включить, кто пользуется ботом (username
 * в Telegram), суммы в ответе, окно дублей (± дней от даты отчёта), допуск
 * цены и кому уходит «Отправить отчёт» из истории грузчика (имя и username).
 * Ответ PUT — вся шапка журнала: экран применяет его, а не перечитывает
 * опрашиваемый статус.
 */
export function BotSettingsModal({
  settings,
  botUsername,
  onClose,
  onSaved,
}: {
  settings: TelegramBotSettings;
  /** Username бота без «@» — подсказка, кому писать /start. */
  botUsername: string;
  onClose: () => void;
  onSaved: (status: TelegramBotStatus) => void;
}) {
  const [enabled, setEnabled] = useState(settings.enabled);
  const [usernames, setUsernames] = useState(settings.allowed_usernames.map((name) => `@${name}`).join("\n"));
  const [showAmounts, setShowAmounts] = useState(settings.show_amounts_in_reply);
  const [windowDays, setWindowDays] = useState(String(settings.duplicate_window_days));
  const [tolerance, setTolerance] = useState(String(Number(settings.price_tolerance_pct)));
  const [reportName, setReportName] = useState(settings.report_recipient_name);
  const [reportUsername, setReportUsername] = useState(
    settings.report_recipient_username ? `@${settings.report_recipient_username}` : "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const allowed = parseUsernames(usernames);
  // Недавно писали боту лично — их username добавляется одним нажатием.
  const suggestions = settings.recent_chats.filter(
    (chat) => chat.type === "private" && chat.username && !allowed.includes(chat.username),
  );
  const botName = botUsername ? `@${botUsername}` : "бота";

  async function save() {
    setBusy(true);
    setError("");
    try {
      const { data } = await api.put<TelegramBotStatus>(`${TELEGRAM_BOT_API}/settings/`, {
        enabled,
        allowed_usernames: allowed,
        show_amounts_in_reply: showAmounts,
        duplicate_window_days: Number(windowDays),
        price_tolerance_pct: tolerance.replace(",", "."),
        report_recipient_name: reportName,
        report_recipient_username: normalizeUsername(reportUsername),
      });
      onSaved(data);
    } catch (cause) {
      setError(apiError(cause));
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
      eyebrow="Telegram-бот"
      title="Настройки бота"
      description="Бот проводит отчёты и отвечает на команды только тем, чей username указан ниже."
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
              Выключен — сообщения ждут в Telegram до суток и проводятся, когда бота снова включат.
            </span>
          </span>
        </label>

        <Field
          label="Кто пользуется ботом"
          htmlFor="bot-usernames"
          hint={`Username в Telegram, по одному в строке: @d1maaash. Человек пишет ${botName} — в личку или в группу, где есть бот.`}
        >
          <Textarea
            mono
            id="bot-usernames"
            rows={3}
            spellCheck={false}
            value={usernames}
            onChange={(event) => setUsernames(event.target.value)}
            className="rounded-md"
          />
        </Field>
        {suggestions.length > 0 && (
          <div className="-mt-1 flex flex-col gap-1.5">
            <span className="text-[12px] text-[var(--muted-foreground)]">Недавно писали боту:</span>
            <div className="flex flex-wrap gap-1.5">
              {suggestions.map((chat) => (
                <Button
                  key={chat.id}
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    setUsernames((current) =>
                      [...parseUsernames(current), chat.username].map((name) => `@${name}`).join("\n"),
                    )
                  }
                >
                  <Plus /> {chat.title ? `${chat.title} (@${chat.username})` : `@${chat.username}`}
                </Button>
              ))}
            </div>
          </div>
        )}
        <p className="-mt-2 text-[12px] text-[var(--muted-foreground)]">
          В группе бот видит отчёты, если в @BotFather у него выключен Group Privacy или он администратор группы.
        </p>

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
              Кнопка в истории грузчика (вкладка «Вагоны»). Бот пишет человеку, только если тот хоть раз написал{" "}
              {botName} (/start); иначе у грузчика откроется её чат в Telegram с готовым текстом.
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
            <Field
              label="Username в Telegram"
              htmlFor="bot-report-username"
              hint={
                settings.report_recipient_username &&
                normalizeUsername(reportUsername) === settings.report_recipient_username
                  ? settings.report_recipient_started
                    ? "Писала боту — отчёты уйдут ботом."
                    : `Ещё не писала ${botName} — попросите нажать /start.`
                  : undefined
              }
            >
              <Input
                id="bot-report-username"
                maxLength={64}
                placeholder="@dinara"
                spellCheck={false}
                autoCapitalize="none"
                value={reportUsername}
                onChange={(event) => setReportUsername(event.target.value)}
                className={PHONE_INPUT_TEXT}
              />
            </Field>
          </div>
        </section>
      </div>
    </Modal>
  );
}
