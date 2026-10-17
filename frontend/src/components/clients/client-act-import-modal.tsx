"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { FileSpreadsheet } from "lucide-react";
import { Button } from "@/components/ui/button";
import { FormError } from "@/components/ui/data-state";
import { InfoRow } from "@/components/ui/info-row";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { ProgressBar } from "@/components/ui/progress-bar";
import { apiError } from "@/lib/api";
import { importClientActChunk, previewClientAct, type ActPreview } from "@/lib/client-acts";
import { finiteMoney } from "@/lib/currency-map";
import { useFileDrop } from "@/lib/use-file-drop";
import { cn, formatCurrency, formatIsoDate, pluralRu } from "@/lib/utils";

type ClientActImportProps = {
  clientId: number;
  open: boolean;
  onClose: () => void;
  /** Акт перенесён: текст уведомления для страницы. Окно после этого закрывается через `onClose`. */
  onImported: (notice: string) => void;
};

const DOCUMENT_FORMS: [string, string, string] = ["документ", "документа", "документов"];

const XLSX_ACCEPT = ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

/** Сальдо словами (дебет − кредит): плюс — клиент должен нам, минус — мы клиенту деньгами или товаром. */
function balanceText(value: string, currency: string): string {
  const amount = finiteMoney(value);
  if (amount > 0) return `клиент должен ${formatCurrency(amount, currency)}`;
  if (amount < 0) return `мы должны клиенту ${formatCurrency(-amount, currency)}`;
  return "полностью оплачено";
}

function Warning({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-md border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-3 py-2 text-sm">
      {children}
    </div>
  );
}

/**
 * «Импорт акта сверки» из 1С. Файл сначала уходит на предпросмотр (ничего не
 * пишется): сервер проверяет раскладку и итоги акта, считает новые документы и
 * пересечение с записями CRM. «Перенести» шлёт тот же файл порциями, пока сервер
 * не ответит `finished`; уже перенесённые документы он пропускает, поэтому
 * обрыв посередине продолжается тем же действием.
 */
export function ClientActImportModal({ open, ...props }: ClientActImportProps) {
  // Каждое открытие — с чистого листа: файл и предпросмотр не переживают закрытие.
  if (!open) return null;
  return <ClientActImportDialog {...props} />;
}

function ClientActImportDialog({ clientId, onClose, onImported }: Omit<ClientActImportProps, "open">) {
  const [file, setFile] = useState<File | null>(null);
  // Брошен или выбран не .xlsx: прежний файл и предпросмотр остаются.
  const [fileError, setFileError] = useState("");
  // «Импортировать с»: `undefined` — дата, которую предложит сервер.
  const [start, setStart] = useState<string | undefined>(undefined);
  const [preview, setPreview] = useState<ActPreview | null>(null);
  const [checking, setChecking] = useState(false);
  const [previewError, setPreviewError] = useState("");
  // Отказ переноса держится до следующей попытки: пересчёт предпросмотра его не стирает.
  const [importError, setImportError] = useState("");
  const [progress, setProgress] = useState<{ done: number; total: number; payments: number } | null>(null);
  const [importing, setImporting] = useState(false);
  // После обрыва переноса предпросмотр пересчитывается: часть документов уже в CRM.
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    if (!file) return;
    const controller = new AbortController();
    setChecking(true);
    previewClientAct(clientId, file, start, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        setPreview(data);
        setPreviewError("");
        setChecking(false);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setPreview(null);
        setPreviewError(apiError(cause) || "Не удалось проверить акт");
        setChecking(false);
      });
    return () => controller.abort();
  }, [clientId, file, start, refresh]);

  const total = preview ? preview.sales.count + preview.payments.count + (preview.opening_order ? 1 : 0) : 0;
  const overlap = preview ? preview.crm.orders + preview.crm.payments + preview.crm.refunds > 0 : false;
  // Прерванный перенос: документы уже в CRM, но деньги актов ещё не разнесены по заказам.
  const pending = preview ? finiteMoney(preview.pending) : 0;
  const canImport = Boolean(
    file && preview && !checking && !importing && (total > 0 || pending > 0) && preview.problems.length === 0,
  );

  function pick(next: File) {
    setFile(next);
    setStart(undefined);
    setPreview(null);
    setProgress(null);
    setPreviewError("");
    setImportError("");
    setFileError("");
  }

  async function runImport() {
    if (!file || !preview || !canImport) return;
    const act = preview;
    setImporting(true);
    setImportError("");
    let payments = 0;
    // Сервер не двигается (ни строк, ни оплат) — не крутимся вечно, а говорим об этом.
    let idle = 0;
    setProgress({ done: 0, total, payments });
    try {
      for (;;) {
        const chunk = await importClientActChunk(clientId, file, act.start);
        payments += chunk.payments;
        setProgress({ done: total - chunk.remaining, total, payments });
        idle = !chunk.finished && chunk.processed === 0 && chunk.payments === 0 ? idle + 1 : 0;
        if (idle >= 3) {
          setImportError("Перенос не продвигается — обновите страницу и откройте импорт ещё раз");
          setProgress(null);
          setRefresh((n) => n + 1);
          return;
        }
        if (chunk.finished) {
          const overpaid = finiteMoney(chunk.overpaid ?? 0);
          onImported(
            `Акт № ${act.act.number} перенесён: ${total} ${pluralRu(total, DOCUMENT_FORMS)}` +
              (overpaid > 0 ? `; мы должны клиенту ${formatCurrency(overpaid, act.act.currency)}` : ""),
          );
          onClose();
          return;
        }
      }
    } catch (cause) {
      // Ошибка остаётся в окне: страница под оверлеем её не покажет.
      setImportError(apiError(cause) || "Перенос прервался — нажмите ещё раз: перенесённое не задвоится");
      setProgress(null);
      setRefresh((n) => n + 1);
    } finally {
      setImporting(false);
    }
  }

  return (
    <Modal
      open
      onClose={() => !importing && onClose()}
      dismissible={!importing}
      className="max-w-lg"
      eyebrow="Клиент"
      title="Импорт акта сверки"
      description="Excel-файл «Акт сверки» из 1С: дебет (что Асыл отпустил) станет отгруженными заказами, кредит (что клиент закрыл) — оплатами долга. Склад не меняется, уже перенесённые документы пропускаются."
      footer={
        <>
          <Button variant="outline" disabled={importing} onClick={onClose}>
            Отмена
          </Button>
          <Button disabled={!canImport} onClick={() => void runImport()}>
            {importing
              ? "Переносим…"
              : total > 0
                ? `Перенести ${total} ${pluralRu(total, DOCUMENT_FORMS)}`
                : pending > 0
                  ? "Довести перенос"
                  : "Перенести"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <ActFileField file={file} disabled={importing} onPick={pick} onReject={setFileError} />

        {checking && !preview && <p className="text-sm text-[var(--muted-foreground)]">Проверяем акт…</p>}

        {preview && <ActSummary preview={preview} total={total} stale={checking} />}

        {preview && !preview.name_matches && (
          <Warning>
            В акте контрагент «{preview.act.counterparty}» — проверьте, что это карточка того же клиента.
          </Warning>
        )}

        {preview && overlap && (
          <Warning>
            <p>
              В CRM за период акта уже есть {preview.crm.orders}{" "}
              {pluralRu(preview.crm.orders, ["заказ", "заказа", "заказов"])} на{" "}
              {formatCurrency(preview.crm.orders_total, preview.act.currency)} и {preview.crm.payments}{" "}
              {pluralRu(preview.crm.payments, ["оплата", "оплаты", "оплат"])} на{" "}
              {formatCurrency(preview.crm.payments_total, preview.act.currency)}
              {preview.crm.refunds > 0 &&
                `, возвратов кассы ${preview.crm.refunds} на ${formatCurrency(preview.crm.refunds_total, preview.act.currency)}`}
              {preview.crm.last_day && ` (последняя запись ${formatIsoDate(preview.crm.last_day)})`}. Чтобы не задвоить,
              документы раньше даты ниже не переносятся.
            </p>
            <div className="mt-2 grid gap-1.5">
              <Label htmlFor="client-act-start">Переносить с</Label>
              <Input
                id="client-act-start"
                type="date"
                value={start ?? preview.start}
                min={preview.act.period_start}
                disabled={importing}
                onChange={(event) => setStart(event.target.value || undefined)}
              />
            </div>
          </Warning>
        )}

        {preview?.problems.map((problem) => (
          <FormError key={problem} message={problem} />
        ))}

        {progress && (
          <div className="grid gap-1.5" aria-live="polite">
            <ProgressBar pct={progress.total ? (progress.done / progress.total) * 100 : 0} />
            <p className="text-xs text-[var(--muted-foreground)]">
              Перенесено {progress.done} из {progress.total} · разнесено оплат: {progress.payments}. Не закрывайте окно.
            </p>
          </div>
        )}

        <FormError message={fileError || importError || previewError} />
      </div>
    </Modal>
  );
}

/**
 * Файл акта: перетащить в любое место окна или выбрать кнопкой. Подходит только
 * .xlsx — `accept` у поля выбора не фильтрует брошенные файлы.
 */
function ActFileField({
  file,
  disabled,
  onPick,
  onReject,
}: {
  file: File | null;
  disabled: boolean;
  onPick: (file: File) => void;
  onReject: (message: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  function take(files: File[]) {
    if (disabled || files.length === 0) return;
    const xlsx = files.find((candidate) => candidate.name.toLowerCase().endsWith(".xlsx"));
    if (xlsx) onPick(xlsx);
    else onReject(`«${files[0].name}» — не .xlsx. Нужен акт сверки из 1С, сохранённый в Excel`);
  }

  const dragging = useFileDrop(take) && !disabled;
  const browse = () => inputRef.current?.click();
  const highlight = dragging && "border-[var(--ring)] bg-[var(--ring)]/5";

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept={XLSX_ACCEPT}
        className="sr-only"
        tabIndex={-1}
        aria-label="Файл акта сверки"
        disabled={disabled}
        onChange={(event) => {
          take(Array.from(event.target.files ?? []));
          event.target.value = "";
        }}
      />
      {file ? (
        <div className={cn("flex items-center gap-3 rounded-lg border px-3 py-2.5 transition-colors", highlight)}>
          <FileSpreadsheet className="size-5 shrink-0 text-emerald-600" />
          <span className="min-w-0 flex-1 truncate text-sm">
            {dragging ? "Отпустите, чтобы заменить файл" : file.name}
          </span>
          <Button type="button" size="sm" variant="outline" disabled={disabled} onClick={browse}>
            Заменить
          </Button>
        </div>
      ) : (
        <button
          type="button"
          disabled={disabled}
          onClick={browse}
          className={cn(
            "flex w-full cursor-pointer flex-col items-center gap-1.5 rounded-lg border border-dashed border-[var(--muted-foreground)]/35 bg-[var(--muted)]/50 px-4 py-8 text-center transition-colors outline-none hover:border-[var(--muted-foreground)]/60 hover:bg-[var(--accent)] focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/50 disabled:pointer-events-none disabled:opacity-50",
            highlight,
          )}
        >
          <FileSpreadsheet className="mb-1 size-7 text-emerald-600" />
          <span className="text-sm font-medium">
            {dragging ? "Отпустите — проверим акт" : "Перетащите сюда акт сверки"}
          </span>
          <span className="text-xs text-[var(--muted-foreground)]">или нажмите, чтобы выбрать файл .xlsx</span>
        </button>
      )}
    </>
  );
}

function ActSummary({ preview, total, stale }: { preview: ActPreview; total: number; stale: boolean }) {
  const { act } = preview;
  const currency = act.currency;
  const differs = finiteMoney(preview.balance_after) !== finiteMoney(act.closing);
  return (
    <section aria-label="Акт" className={cn("grid gap-3", stale && "opacity-60")}>
      <div className="text-sm">
        <div className="font-medium">
          Акт № {act.number || "—"} · {formatIsoDate(act.period_start)} – {formatIsoDate(act.period_end)} · {currency}
        </div>
        {act.counterparty && <div className="text-[var(--muted-foreground)]">{act.counterparty}</div>}
      </div>
      <div className="rounded-lg border px-3 text-sm">
        <InfoRow label="Дебет → заказы">
          {preview.sales.count} · {formatCurrency(preview.sales.total, currency)}
        </InfoRow>
        <InfoRow label="Кредит → оплаты">
          {preview.payments.count} · {formatCurrency(preview.payments.total, currency)}
        </InfoRow>
        {preview.offsets.count > 0 && (
          <InfoRow label="из них зачёт по акту (возвраты и др.)">
            {preview.offsets.count} · {formatCurrency(preview.offsets.total, currency)}
          </InfoRow>
        )}
        {preview.opening_order && (
          <InfoRow label="Сальдо на начало → заказ">{formatCurrency(preview.opening_order, currency)}</InfoRow>
        )}
        {preview.imported > 0 && (
          <InfoRow label="Уже перенесено раньше">
            {preview.imported} {pluralRu(preview.imported, DOCUMENT_FORMS)}
          </InfoRow>
        )}
        {preview.before_start > 0 && (
          <InfoRow label="Раньше даты начала">
            {preview.before_start} {pluralRu(preview.before_start, DOCUMENT_FORMS)} — пропуск
          </InfoRow>
        )}
        <InfoRow label="Сальдо по акту">{balanceText(act.closing, currency)}</InfoRow>
        <InfoRow label="В CRM после переноса">
          <span className={cn(differs && "text-[var(--warning)]")}>{balanceText(preview.balance_after, currency)}</span>
        </InfoRow>
      </div>
      {total === 0 && preview.problems.length === 0 && finiteMoney(preview.pending) > 0 && (
        <p className="text-sm text-[var(--muted-foreground)]">
          Перенос не закончен: документы уже в CRM, осталось разнести по заказам{" "}
          {formatCurrency(preview.pending, currency)}.
        </p>
      )}
      {total === 0 && preview.problems.length === 0 && finiteMoney(preview.pending) <= 0 && (
        <p className="text-sm text-[var(--muted-foreground)]">Все документы акта уже в CRM — переносить нечего.</p>
      )}
      {differs && total > 0 && (
        <p className="text-xs text-[var(--muted-foreground)]">
          Сальдо CRM не совпадёт с актом: у клиента есть записи, которых нет в акте, или акт начат не с начала.
        </p>
      )}
    </section>
  );
}
