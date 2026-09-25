"use client";
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, ClipboardPaste, LoaderCircle, PackageCheck, TrainFront } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, apiError } from "@/lib/api";
import type { LoaderOrder } from "@/lib/loader";
import {
  RAIL_REPORT_API,
  RAIL_REPORT_MAX_LENGTH,
  railReportBody,
  reportDayLabel,
  WAGON_NUMBER_PROBLEMS,
  type RailIssue,
  type RailOptions,
  type RailPreview,
} from "@/lib/rail-report";
import { useApi } from "@/lib/use-api";
import { bagsWord, cn, formatCurrency, formatMoney, PHONE_INPUT_TEXT } from "@/lib/utils";
import { wagonsWord } from "@/lib/wagons";

type Busy = "check" | "remember" | "apply" | null;
type RememberPath = "product-codes" | "client-names";

const PLACEHOLDER = "сб 19.09.26 Узбекистан ООО OSIYO NAV NIHOL\nСт. Раустан 12 вагон\nД1с-28087658-68 тн\n…";

/**
 * «Вставить отчёт» во вкладке «Вагоны»: текст отчёта из WhatsApp →
 * предпросмотр без записи → разрешение неизвестного (сохраняет словари) →
 * «Провести». С ``orderId`` — «Отгрузить по отчёту» заранее внесённый заказ.
 * Ошибки — внутри листа, рядом с действием: «Проверить»/«Провести» — над
 * кнопками внизу (длинный отчёт не прокручивает их из вида), «Запомнить» —
 * в своём разделе. Ответ «Провести» — строка для экрана (у грузчика — строка
 * истории, в журнале бота — строка журнала).
 *
 * Журнал WhatsApp-бота открывает тот же лист с текстом сообщения
 * (``initialText`` — сразу проверяется) и своими адресами (``api``).
 */
export function RailReportSheet<T = LoaderOrder>({
  orderId: initialOrderId = null,
  api: apiBase = RAIL_REPORT_API,
  initialText = "",
  title,
  eyebrow,
  onClose,
  onApplied,
}: {
  orderId?: number | null;
  /** Адреса предпросмотра, словарей и «Провести»: `${api}/preview/` и т. д. */
  api?: string;
  initialText?: string;
  title?: string;
  eyebrow?: string;
  onClose: () => void;
  onApplied: (row: T) => void;
}) {
  const [text, setText] = useState(initialText);
  const [orderId, setOrderId] = useState<number | null>(initialOrderId);
  const [preview, setPreview] = useState<RailPreview | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState("");
  const [rememberError, setRememberError] = useState<{ path: RememberPath; message: string } | null>(null);
  const [editingClient, setEditingClient] = useState(false);

  const needsProducts = Boolean(preview?.can_remember_products && preview.unresolved.products.length);
  const needsClient = Boolean(preview?.can_remember_clients && (preview.unresolved.client || editingClient));
  const options = useApi<RailOptions>(needsProducts || needsClient ? `${apiBase}/options/` : null);

  async function check(forOrder = orderId, { keepError = false } = {}) {
    if (!text.trim()) {
      setError("Вставьте текст отчёта");
      return;
    }
    setBusy("check");
    if (!keepError) setError("");
    setRememberError(null);
    try {
      const { data } = await api.post<RailPreview>(`${apiBase}/preview/`, railReportBody(text, forOrder));
      setPreview(data);
      setEditingClient(false);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(null);
    }
  }

  /** Запомнить код товара или клиента и применить свежий предпросмотр из ответа. */
  async function remember(path: RememberPath, body: Record<string, unknown>) {
    setBusy("remember");
    setError("");
    setRememberError(null);
    try {
      const { data } = await api.post<RailPreview>(`${apiBase}/${path}/`, {
        ...railReportBody(text, orderId),
        ...body,
      });
      setPreview(data);
      setEditingClient(false);
    } catch (cause) {
      setRememberError({ path, message: apiError(cause) });
    } finally {
      setBusy(null);
    }
  }

  async function apply() {
    if (!preview?.can_apply || busy) return;
    setBusy("apply");
    setError("");
    try {
      const { data } = await api.post<T>(`${apiBase}/apply/`, railReportBody(text, orderId));
      onApplied(data);
    } catch (cause) {
      setError(apiError(cause));
      setBusy(null);
      // Пока лист был открыт, отчёт мог провести бот или коллега — покажем свежие причины.
      void check(orderId, { keepError: true });
    }
  }

  // Текст сообщения из журнала бота проверяется сразу при открытии — один раз.
  const checkedInitial = useRef(false);
  useEffect(() => {
    if (checkedInitial.current || !initialText.trim()) return;
    checkedInitial.current = true;
    void check();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- только при открытии листа
  }, []);

  /** Похожий ручной заказ — отгрузить его по отчёту; ``null`` — обратно к новому заказу. */
  function switchToOrder(id: number | null) {
    setOrderId(id);
    void check(id);
  }

  const applyLabel = orderId === null ? "Провести" : "Отгрузить по отчёту";
  return (
    <Modal
      open
      onClose={onClose}
      variant="sheet"
      className="max-w-3xl"
      eyebrow={eyebrow ?? (orderId === null ? "Вагоны · отчёт из WhatsApp" : `Вагоны · заказ №${orderId}`)}
      title={title ?? (orderId === null ? "Вставить отчёт" : "Отгрузить по отчёту")}
      footer={
        // На телефоне три кнопки в строку не влезают: переносятся и тянутся на всю ширину.
        <div className="flex flex-1 flex-wrap items-center justify-end gap-2">
          {error && (
            <p
              role="alert"
              className="max-h-24 basis-full overflow-y-auto rounded-lg bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]"
            >
              {error}
            </p>
          )}
          <Button variant="ghost" className="max-sm:grow" disabled={busy === "apply"} onClick={onClose}>
            Закрыть
          </Button>
          {preview && !preview.ok && (
            // Заказ или словарь поправили в другом окне — проверить тот же текст ещё раз.
            <Button variant="outline" className="max-sm:grow" disabled={busy !== null} onClick={() => void check()}>
              {busy === "check" && <LoaderCircle className="animate-spin" />}
              Проверить снова
            </Button>
          )}
          {preview ? (
            <Button className="max-sm:grow" disabled={!preview.can_apply || busy !== null} onClick={() => void apply()}>
              {busy === "apply" ? <LoaderCircle className="animate-spin" /> : <PackageCheck />}
              {busy === "apply" ? "Проводим…" : applyLabel}
            </Button>
          ) : (
            <Button className="max-sm:grow" disabled={busy !== null || !text.trim()} onClick={() => void check()}>
              {busy === "check" ? <LoaderCircle className="animate-spin" /> : <ClipboardPaste />}
              Проверить
            </Button>
          )}
        </div>
      }
    >
      <div className="flex min-w-0 flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="rail-report-text">Текст отчёта</Label>
          <Textarea
            mono
            id="rail-report-text"
            autoFocus
            rows={preview ? 4 : 8}
            maxLength={RAIL_REPORT_MAX_LENGTH}
            spellCheck={false}
            value={text}
            placeholder={PLACEHOLDER}
            onChange={(event) => {
              setText(event.target.value);
              // Предпросмотр — про прежний текст: провести можно только проверенное.
              setPreview(null);
              setError("");
              setRememberError(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                void check();
              }
            }}
          />
        </div>

        {initialOrderId === null && orderId !== null && (
          <Button
            variant="link"
            size="sm"
            className="h-auto self-start px-0"
            disabled={busy !== null}
            onClick={() => switchToOrder(null)}
          >
            ← Провести отчёт новым заказом
          </Button>
        )}

        {preview && (
          <PreviewBody
            preview={preview}
            options={options.data}
            optionsError={options.error}
            needsProducts={needsProducts}
            needsClient={needsClient}
            rememberError={rememberError}
            busy={busy}
            editingClient={editingClient}
            onEditClient={() => setEditingClient(true)}
            onRemember={remember}
            onSwitchToOrder={switchToOrder}
          />
        )}
      </div>
    </Modal>
  );
}

function PreviewBody({
  preview,
  options,
  optionsError,
  needsProducts,
  needsClient,
  rememberError,
  busy,
  editingClient,
  onEditClient,
  onRemember,
  onSwitchToOrder,
}: {
  preview: RailPreview;
  options: RailOptions | null;
  optionsError: string;
  /** Выбор товара или клиента: от тех же условий лист загружает словари. */
  needsProducts: boolean;
  needsClient: boolean;
  rememberError: { path: RememberPath; message: string } | null;
  busy: Busy;
  editingClient: boolean;
  onEditClient: () => void;
  onRemember: (path: RememberPath, body: Record<string, unknown>) => Promise<void>;
  onSwitchToOrder: (orderId: number) => void;
}) {
  const { totals } = preview;
  // Отгрузить по отчёту можно только ручной заказ, который ещё ждёт отгрузки.
  const duplicates = preview.order_id === null ? preview.shippable_orders : [];
  const errorOf = (path: RememberPath) => optionsError || (rememberError?.path === path ? rememberError.message : "");
  const optionsLoading = !options && !optionsError;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <section aria-label="Итог отчёта" className="rounded-xl border bg-[var(--muted)]/30 p-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="text-sm font-semibold">{reportDayLabel(preview.day)}</span>
          {preview.client ? (
            <span className="min-w-0 truncate text-sm font-semibold">{preview.client.name}</span>
          ) : (
            <span className="text-sm text-[var(--destructive)]">
              Клиент не найден{preview.client_name && `: «${preview.client_name}»`}
            </span>
          )}
          {preview.currency && <Badge tone="outline">{preview.currency}</Badge>}
          {preview.client && preview.can_remember_clients && !editingClient && (
            <Button variant="link" size="sm" className="h-auto px-0" onClick={onEditClient}>
              Другой клиент или валюта
            </Button>
          )}
        </div>
        <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
          <span className="inline-flex items-center gap-1.5">
            <TrainFront className="size-4" />
            {preview.station ? `ст. ${preview.station}` : "станция не указана"}
          </span>
          <span className="tabular-nums">
            <b>{totals.wagons}</b> {wagonsWord(totals.wagons)}
          </span>
          <span className="tabular-nums">
            <b>{formatMoney(totals.tons)}</b> т
          </span>
          <span className="tabular-nums">
            <b>{formatMoney(totals.bags)}</b> {bagsWord(totals.bags)}
          </span>
          {totals.amount !== null && (
            <span className="font-semibold tabular-nums">{formatCurrency(totals.amount, totals.currency)}</span>
          )}
        </div>
      </section>

      {preview.issues.length > 0 && (
        <IssueList
          tone="destructive"
          title={preview.ok ? "" : "Провести нельзя — нужно исправить"}
          issues={preview.issues}
        />
      )}
      {duplicates.map((id) => (
        <Button
          key={id}
          variant="outline"
          className="self-start"
          disabled={busy !== null}
          onClick={() => onSwitchToOrder(id)}
        >
          <PackageCheck /> Отгрузить заказ №{id} по этому отчёту
        </Button>
      ))}
      {preview.warnings.length > 0 && <IssueList tone="warning" title="Обратите внимание" issues={preview.warnings} />}

      {needsProducts ? (
        <ProductCodes
          codes={preview.unresolved.products}
          products={options?.products ?? []}
          loading={optionsLoading}
          error={errorOf("product-codes")}
          busy={busy}
          onRemember={(code, product) => onRemember("product-codes", { code, product })}
        />
      ) : (
        preview.unresolved.products.length > 0 && (
          <p className="text-sm text-[var(--muted-foreground)]">
            Код товара запоминает сотрудник, который правит товары или создаёт заказы.
          </p>
        )
      )}
      {needsClient ? (
        <ClientPicker
          name={preview.client_name}
          current={preview.client?.id ?? null}
          currency={preview.currency}
          clients={options?.clients ?? []}
          loading={optionsLoading}
          error={errorOf("client-names")}
          busy={busy}
          onRemember={(client, currency) =>
            onRemember("client-names", { client_name: preview.client_name, client, currency })
          }
        />
      ) : (
        preview.unresolved.client &&
        preview.order_id === null && (
          <p className="text-sm text-[var(--muted-foreground)]">
            Клиента отчёта выбирает сотрудник, который создаёт заказы.
          </p>
        )
      )}

      <WagonsTable preview={preview} />

      {preview.ok && !preview.can_apply && (
        <p className="text-sm text-[var(--muted-foreground)]">
          {preview.order_id === null
            ? "Провести может сотрудник с правом отгрузки вагонов, создания и подтверждения заказов."
            : "Отгрузить может сотрудник с правом отгрузки вагонов."}
        </p>
      )}
      {preview.ok && preview.can_apply && (
        <p className="flex items-center gap-1.5 text-sm text-[var(--success)]">
          <CheckCircle2 className="size-4" /> Всё сошлось — можно проводить.
        </p>
      )}
    </div>
  );
}

/** Причины разбора отчёта (с номером строки) — и в листе, и в журнале WhatsApp-бота. */
export function IssueList({
  tone,
  title,
  issues,
}: {
  tone: "destructive" | "warning";
  title: string;
  issues: RailIssue[];
}) {
  return (
    <div
      role={tone === "destructive" ? "alert" : "status"}
      className={cn(
        "rounded-xl border px-4 py-3 text-sm",
        tone === "destructive"
          ? "border-[var(--destructive)]/30 bg-[var(--destructive)]/10"
          : "border-[var(--warning)]/40 bg-[var(--warning)]/10",
      )}
    >
      {title && (
        <div className="mb-1.5 flex items-center gap-1.5 font-semibold">
          <AlertTriangle className="size-4" /> {title}
        </div>
      )}
      <ul className="flex flex-col gap-1">
        {issues.map((issue, index) => (
          <li key={`${issue.code}-${index}`}>
            {issue.line !== null && (
              <span className="mr-1.5 text-xs text-[var(--muted-foreground)] tabular-nums">строка {issue.line}</span>
            )}
            {issue.message}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ProductCodes({
  codes,
  products,
  loading,
  error,
  busy,
  onRemember,
}: {
  codes: string[];
  products: RailOptions["products"];
  loading: boolean;
  error: string;
  busy: Busy;
  onRemember: (code: string, product: number) => Promise<void>;
}) {
  const [picks, setPicks] = useState<Record<string, string>>({});
  return (
    <section aria-label="Неизвестные коды товара" className="flex flex-col gap-3 rounded-xl border p-4">
      <div className="text-sm font-semibold">Какой это товар? Код запомнится для следующих отчётов.</div>
      {error && (
        <p role="alert" className="text-sm text-[var(--destructive)]">
          {error}
        </p>
      )}
      {codes.map((code, index) => {
        // Код может содержать пробелы и кавычки — id поля по порядку.
        const id = `rail-code-${index}`;
        return (
          <div key={code} className="flex flex-col gap-1.5 sm:flex-row sm:items-center">
            <Label htmlFor={id} className="shrink-0 font-mono sm:mb-0 sm:w-24">
              «{code}»
            </Label>
            <Select
              id={id}
              className={PHONE_INPUT_TEXT}
              value={picks[code] ?? ""}
              disabled={loading || busy !== null}
              onChange={(event) => setPicks((current) => ({ ...current, [code]: event.target.value }))}
            >
              <option value="">{loading ? "Загрузка…" : "Выберите товар"}</option>
              {products.map((product) => (
                <option key={product.id} value={product.id}>
                  {product.label}
                </option>
              ))}
            </Select>
            <Button
              variant="outline"
              className="shrink-0"
              disabled={!picks[code] || busy !== null}
              onClick={() => void onRemember(code, Number(picks[code]))}
            >
              Запомнить
            </Button>
          </div>
        );
      })}
    </section>
  );
}

function ClientPicker({
  name,
  current,
  currency: currentCurrency,
  clients,
  loading,
  error,
  busy,
  onRemember,
}: {
  name: string;
  current: number | null;
  currency: string;
  clients: RailOptions["clients"];
  loading: boolean;
  error: string;
  busy: Busy;
  onRemember: (client: number, currency: string) => Promise<void>;
}) {
  const [client, setClient] = useState(current === null ? "" : String(current));
  const [currency, setCurrency] = useState(currentCurrency);
  return (
    <section aria-label="Клиент отчёта" className="flex flex-col gap-3 rounded-xl border p-4">
      <div className="text-sm font-semibold">Кто это — «{name}»? Клиент и валюта запомнятся для следующих отчётов.</div>
      {error && (
        <p role="alert" className="text-sm text-[var(--destructive)]">
          {error}
        </p>
      )}
      <div className="flex flex-col gap-2 sm:flex-row">
        <Select
          aria-label="Клиент"
          className={PHONE_INPUT_TEXT}
          value={client}
          disabled={loading || busy !== null}
          onChange={(event) => {
            setClient(event.target.value);
            // Валюта по умолчанию — валюта клиента; поменять можно тут же.
            const picked = clients.find((item) => String(item.id) === event.target.value);
            if (picked) setCurrency(picked.currency);
          }}
        >
          <option value="">{loading ? "Загрузка…" : "Выберите клиента"}</option>
          {clients.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
              {item.department_name ? ` · ${item.department_name}` : ""}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Валюта"
          className={cn(PHONE_INPUT_TEXT, "sm:w-28")}
          value={currency}
          disabled={busy !== null}
          onChange={(event) => setCurrency(event.target.value)}
        >
          <option value="">Валюта</option>
          <option value="USD">USD</option>
          <option value="KZT">KZT</option>
        </Select>
        <Button
          variant="outline"
          className="shrink-0"
          disabled={!client || !currency || busy !== null}
          onClick={() => void onRemember(Number(client), currency)}
        >
          Запомнить
        </Button>
      </div>
    </section>
  );
}

function WagonsTable({ preview }: { preview: RailPreview }) {
  if (preview.wagons.length === 0) return null;
  const currency = preview.currency;
  return (
    <div className="min-w-0 overflow-x-auto rounded-xl border">
      <table aria-label="Вагоны отчёта" className="w-full text-sm">
        <thead className="bg-[var(--muted)]/60 text-left text-xs text-[var(--muted-foreground)]">
          <tr>
            <th className="px-3 py-2 font-medium">№</th>
            <th className="px-3 py-2 font-medium">Вагон</th>
            <th className="px-3 py-2 font-medium">Товар</th>
            <th className="px-3 py-2 text-right font-medium">Тонн</th>
            <th className="px-3 py-2 text-right font-medium">Мешков</th>
            <th className="px-3 py-2 text-right font-medium">Цена</th>
            <th className="px-3 py-2 text-right font-medium">Сумма</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {preview.wagons.map((wagon) => (
            <tr key={`${wagon.position}-${wagon.number}`}>
              <td className="px-3 py-1.5 tabular-nums text-[var(--muted-foreground)]">{wagon.position}</td>
              <td className="whitespace-nowrap px-3 py-1.5">
                <span className="font-mono tabular-nums">{wagon.number}</span>
                {wagon.number_status === "ok" ? (
                  <CheckCircle2 aria-label="номер верный" className="ml-1.5 inline size-3.5 text-[var(--success)]" />
                ) : (
                  <span className="ml-1.5 text-xs font-medium text-[var(--destructive)]">
                    {WAGON_NUMBER_PROBLEMS[wagon.number_status]}
                  </span>
                )}
              </td>
              <td className="min-w-40 px-3 py-1.5">
                {wagon.product_label || (
                  <span className="text-[var(--destructive)]">
                    неизвестный код <span className="font-mono">«{wagon.code}»</span>
                  </span>
                )}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums">{formatMoney(wagon.tons)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{wagon.bags ?? "—"}</td>
              <td className="whitespace-nowrap px-3 py-1.5 text-right tabular-nums">
                {wagon.unit_price === null ? "—" : formatCurrency(wagon.unit_price, currency)}
              </td>
              <td className="whitespace-nowrap px-3 py-1.5 text-right tabular-nums">
                {wagon.amount === null ? "—" : formatCurrency(wagon.amount, currency)}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot className="border-t bg-[var(--muted)]/30 font-semibold">
          <tr>
            <td className="px-3 py-2" colSpan={3}>
              Итого
            </td>
            <td className="px-3 py-2 text-right tabular-nums">{formatMoney(preview.totals.tons)}</td>
            <td className="px-3 py-2 text-right tabular-nums">{formatMoney(preview.totals.bags)}</td>
            <td className="px-3 py-2" />
            <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
              {preview.totals.amount === null ? "—" : formatCurrency(preview.totals.amount, preview.totals.currency)}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
