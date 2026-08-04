"use client";

import { useEffect, useMemo, useState } from "react";
import { Building2, Check, Download, FileDown, Layers, Minus } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import { monthStartLocalIsoDate, todayLocalIsoDate } from "@/lib/utils";
import { useApi } from "@/lib/use-api";
import type { Department } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";

/** Раздел выписки. Ключ совпадает с backend (`CLIENT_SECTIONS`). */
export type StatementSection = {
  key: string;
  name: string;
  hint: string;
};

const FORMATS = [
  { key: "xlsx" as const, name: "Excel (.xlsx)", hint: "Листы с формулами, для работы с данными" },
  { key: "pdf" as const, name: "PDF (.pdf)", hint: "Готов к печати и отправке клиенту" },
];

/** Разделы выписки по одному клиенту. Порядок — как листы в книге. */
export const CLIENT_STATEMENT_SECTIONS: StatementSection[] = [
  { key: "summary", name: "Сводка", hint: "Реквизиты и блок сверки остатков" },
  { key: "ledger", name: "Операции", hint: "Лента отгрузок и оплат с остатком" },
  { key: "orders", name: "Заказы", hint: "Заказы периода по дате создания" },
  { key: "items", name: "Позиции", hint: "Товары, мешки и цены построчно" },
  { key: "payments", name: "Платежи", hint: "Поступления со статусом и способом" },
  { key: "debts", name: "Долги", hint: "Непогашенные остатки на момент выгрузки" },
];

/** Разделы общей выписки: добавляется разрез по клиентам. */
export const ALL_CLIENTS_STATEMENT_SECTIONS: StatementSection[] = [
  CLIENT_STATEMENT_SECTIONS[0],
  { key: "clients", name: "Клиенты", hint: "Итоги по каждому контрагенту" },
  ...CLIENT_STATEMENT_SECTIONS.slice(1),
];

type Props = {
  open: boolean;
  onClose: () => void;
  endpoint: string;
  filename: string;
  title: string;
  description: string;
  scopeLabel: string;
  sections?: StatementSection[];
  initialFrom?: string;
  initialTo?: string;
};

export function StatementExportModal({
  open,
  onClose,
  endpoint,
  filename,
  title,
  description,
  scopeLabel,
  sections = CLIENT_STATEMENT_SECTIONS,
  initialFrom = "",
  initialTo = "",
}: Props) {
  const [dateFrom, setDateFrom] = useState(initialFrom);
  const [dateTo, setDateTo] = useState(initialTo);
  // Excel — для работы с данными, PDF — на печать и отправку клиенту.
  // Разделы и отделы общие: выбор один, форматов два.
  const [format, setFormat] = useState<"xlsx" | "pdf">("xlsx");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selectedDepartments, setSelectedDepartments] = useState<Set<string>>(new Set());
  const [selectedSections, setSelectedSections] = useState<Set<string>>(
    () => new Set(sections.map((section) => section.key)),
  );
  const {
    data: departments,
    loading: departmentsLoading,
    error: departmentsError,
    reload: reloadDepartments,
  } = useApi<Department[]>(open ? "/departments/?all=1" : null);
  const departmentKey = departments?.map((department) => department.code).join("|") ?? "";
  const sectionKey = sections.map((section) => section.key).join("|");

  useEffect(() => {
    if (!open) return;
    setDateFrom(initialFrom || monthStartLocalIsoDate());
    setDateTo(initialTo || todayLocalIsoDate());
    setError("");
  }, [open, initialFrom, initialTo]);

  useEffect(() => {
    if (!open || !departments) return;
    setSelectedDepartments(new Set(departments.map((department) => department.code)));
  }, [open, departments, departmentKey]);

  // Список разделов зависит от экрана (карточка клиента / общая выписка):
  // при смене набора выбор сбрасывается, иначе в запрос уехал бы ключ,
  // которого нет в этом эндпоинте, и бэкенд ответил бы 400.
  useEffect(() => {
    if (!open) return;
    setSelectedSections(new Set(sections.map((section) => section.key)));
  }, [open, sectionKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedRows = useMemo(
    () => departments?.filter((department) => selectedDepartments.has(department.code)) ?? [],
    [departments, selectedDepartments],
  );

  function toggleDepartment(code: string) {
    setSelectedDepartments((current) => {
      const next = new Set(current);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
    setError("");
  }

  function toggleSection(key: string) {
    setSelectedSections((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    setError("");
  }

  function datedFilename() {
    const stem = filename.replace(/\.(xlsx|pdf)$/i, "");
    const period =
      dateFrom || dateTo
        ? `${dateFrom || "start"}_${dateTo || todayLocalIsoDate()}`
        : `all-time_${todayLocalIsoDate()}`;
    return `${stem}_${period}.${format}`;
  }

  const allSectionsChosen = selectedSections.size === sections.length;

  async function download() {
    if (!selectedDepartments.size) {
      setError("Выберите хотя бы один отдел для выписки.");
      return;
    }
    if (!selectedSections.size) {
      setError("Выберите хотя бы один раздел выписки.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await api.get(endpoint, {
        params: {
          ...(dateFrom ? { date_from: dateFrom } : {}),
          ...(dateTo ? { date_to: dateTo } : {}),
          departments: Array.from(selectedDepartments).join(","),
          // Параметр называется export, а не format: последнее зарезервировано
          // DRF под согласование типа ответа и до вью не доходит.
          ...(format === "pdf" ? { export: "pdf" } : {}),
          // Полный набор не отправляем: пустой параметр — «вся выписка»,
          // и ссылка остаётся такой же, как до появления выбора разделов.
          ...(allSectionsChosen
            ? {}
            : {
                sections: sections
                  .filter((section) => selectedSections.has(section.key))
                  .map((section) => section.key)
                  .join(","),
              }),
        },
        responseType: "blob",
      });
      downloadBlob(response.data, datedFilename());
      onClose();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  const periodPresets: { label: string; apply: () => void }[] = [
    {
      label: "Всё время",
      apply: () => {
        setDateFrom("");
        setDateTo("");
      },
    },
    {
      label: "Этот месяц",
      apply: () => {
        setDateFrom(monthStartLocalIsoDate());
        setDateTo(todayLocalIsoDate());
      },
    },
    {
      label: "Сегодня",
      apply: () => {
        const value = todayLocalIsoDate();
        setDateFrom(value);
        setDateTo(value);
      },
    },
  ];

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Финансы · Выписка"
      title={title}
      description={description}
      className="max-w-2xl"
      mobileFullscreen
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Отмена
          </Button>
          <Button
            onClick={() => void download()}
            disabled={busy || departmentsLoading || !selectedDepartments.size || !selectedSections.size}
          >
            <Download className="size-4" /> {busy ? "Формирование…" : `Скачать .${format}`}
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        {/* Итог сверху: что именно уедет в файл, одной строкой. */}
        <div className="rounded-xl border border-[var(--border)] bg-[var(--muted)] px-4 py-3">
          <div className="text-sm font-semibold text-[var(--foreground)]">{scopeLabel}</div>
          <div className="mt-1 text-xs text-[var(--muted-foreground)]">
            {selectedSections.size} из {sections.length} разделов · отделов: {selectedRows.length}
          </div>
        </div>

        <Section
          icon={<FileDown className="size-3.5" />}
          title="Формат файла"
          note="Разделы и период общие — меняется только то, во что они лягут."
        >
          <div className="grid gap-1.5 sm:grid-cols-2">
            {FORMATS.map((option) => (
              <CheckRow
                key={option.key}
                selected={format === option.key}
                label={option.name}
                hint={option.hint}
                onClick={() => {
                  setFormat(option.key);
                  setError("");
                }}
              />
            ))}
          </div>
        </Section>

        <Section
          icon={<Layers className="size-3.5" />}
          title="Разделы выписки"
          note="Каждый раздел — отдельный лист книги."
          action={
            <SelectAll
              allChosen={allSectionsChosen}
              scope="разделы"
              onAll={() => setSelectedSections(new Set(sections.map((section) => section.key)))}
              onNone={() => setSelectedSections(new Set())}
            />
          }
        >
          <div className="grid gap-1.5 sm:grid-cols-2">
            {sections.map((section) => (
              <CheckRow
                key={section.key}
                selected={selectedSections.has(section.key)}
                label={section.name}
                hint={section.hint}
                onClick={() => toggleSection(section.key)}
              />
            ))}
          </div>
        </Section>

        <Section
          icon={<Building2 className="size-3.5" />}
          title="Отделы"
          note="Заказы, оплаты и долги фильтруются по выбранным отделам."
          action={
            !!departments?.length && (
              <SelectAll
                allChosen={selectedDepartments.size === departments.length}
                scope="отделы"
                onAll={() => setSelectedDepartments(new Set(departments.map((d) => d.code)))}
                onNone={() => setSelectedDepartments(new Set())}
              />
            )
          }
        >
          {departmentsLoading && !departments && (
            <p className="text-sm text-[var(--muted-foreground)]">Загружаем список отделов…</p>
          )}
          {departmentsError && (
            <div className="flex items-center justify-between gap-3 rounded-lg border border-[var(--destructive)]/25 bg-[var(--destructive)]/5 px-3 py-2.5 text-sm text-[var(--destructive)]">
              <span>{departmentsError}</span>
              <Button type="button" size="sm" variant="outline" onClick={() => void reloadDepartments()}>
                Повторить
              </Button>
            </div>
          )}
          {!!departments?.length && (
            <div className="grid gap-1.5 sm:grid-cols-2">
              {departments.map((department) => (
                <CheckRow
                  key={department.code}
                  selected={selectedDepartments.has(department.code)}
                  label={department.name}
                  hint={department.is_active ? undefined : "Архивный отдел"}
                  dot={department.color}
                  onClick={() => toggleDepartment(department.code)}
                />
              ))}
            </div>
          )}
          {!departmentsLoading && departments?.length === 0 && (
            <p className="rounded-lg bg-[var(--warning)]/10 px-3 py-2.5 text-sm text-[var(--foreground)]">
              Нет отделов для формирования выписки.
            </p>
          )}
        </Section>

        <Section
          icon={<Minus className="size-3.5 rotate-90" />}
          title="Период"
          note="Продажи — по дате отгрузки, оплаты — по дате подтверждения кассой."
        >
          <div className="mb-3 flex flex-wrap gap-1.5">
            {periodPresets.map((preset) => (
              <button
                key={preset.label}
                type="button"
                onClick={preset.apply}
                className="rounded-lg border border-[var(--border)] px-2.5 py-1.5 text-xs font-medium text-[var(--foreground)] transition hover:bg-[var(--muted)]"
              >
                {preset.label}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className="grid gap-1.5 text-xs font-medium text-[var(--muted-foreground)]">
              С даты
              <Input
                type="date"
                value={dateFrom}
                max={dateTo || undefined}
                onChange={(event) => setDateFrom(event.target.value)}
              />
            </label>
            <label className="grid gap-1.5 text-xs font-medium text-[var(--muted-foreground)]">
              По дату
              <Input
                type="date"
                value={dateTo}
                min={dateFrom || undefined}
                onChange={(event) => setDateTo(event.target.value)}
              />
            </label>
          </div>
          <p className="mt-2.5 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
            Долг, накопленный до начала периода, попадает в блок сверки как остаток на начало. Лист «Долги» показывает
            текущий остаток на момент выгрузки.
          </p>
        </Section>

        {error && (
          <p className="rounded-lg border border-[var(--destructive)]/25 bg-[var(--destructive)]/5 px-3 py-2.5 text-sm font-medium text-[var(--destructive)]">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}

function Section({
  icon,
  title,
  note,
  action,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  note: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-2.5 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
            {icon}
            {title}
          </h3>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">{note}</p>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function SelectAll({
  allChosen,
  scope,
  onAll,
  onNone,
}: {
  allChosen: boolean;
  /** Что переключаем — попадает в доступное имя, иначе на экране две
   *  одинаковые кнопки «Снять все» и скринридер их не различает. */
  scope: string;
  onAll: () => void;
  onNone: () => void;
}) {
  const label = allChosen ? "Снять все" : "Выбрать все";
  return (
    <button
      type="button"
      onClick={allChosen ? onNone : onAll}
      aria-label={`${label}: ${scope}`}
      className="text-xs font-medium text-[var(--muted-foreground)] underline-offset-4 transition hover:text-[var(--foreground)] hover:underline"
    >
      {label}
    </button>
  );
}

function CheckRow({
  selected,
  label,
  hint,
  dot,
  onClick,
}: {
  selected: boolean;
  label: string;
  hint?: string;
  dot?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={`flex min-h-11 items-center gap-2.5 rounded-lg border px-3 py-2 text-left transition ${
        selected
          ? "border-[var(--foreground)]/20 bg-[var(--muted)]"
          : "border-[var(--border)] bg-transparent hover:border-[var(--foreground)]/20"
      }`}
    >
      <span
        aria-hidden
        className={`flex size-4 shrink-0 items-center justify-center rounded border transition ${
          selected
            ? "border-[var(--primary)] bg-[var(--primary)] text-[var(--primary-foreground)]"
            : "border-[var(--input)]"
        }`}
      >
        {selected && <Check className="size-3" strokeWidth={3} />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          {dot && <span className="size-2 shrink-0 rounded-full" style={{ backgroundColor: dot }} />}
          <span
            className={`truncate text-sm ${selected ? "font-semibold text-[var(--foreground)]" : "text-[var(--muted-foreground)]"}`}
          >
            {label}
          </span>
        </span>
        {hint && <span className="mt-0.5 block truncate text-[11px] text-[var(--muted-foreground)]">{hint}</span>}
      </span>
    </button>
  );
}
