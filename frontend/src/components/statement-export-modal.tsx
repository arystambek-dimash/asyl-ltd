"use client";

import { useEffect, useMemo, useState } from "react";
import { Building2, CalendarRange, Check, Download, FileSpreadsheet } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import { monthStartLocalIsoDate, todayLocalIsoDate } from "@/lib/utils";
import { useApi } from "@/lib/use-api";
import type { Department } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";

type Props = {
  open: boolean;
  onClose: () => void;
  endpoint: string;
  filename: string;
  title: string;
  description: string;
  scopeLabel: string;
  sheetsLabel: string;
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
  sheetsLabel,
  initialFrom = "",
  initialTo = "",
}: Props) {
  const [dateFrom, setDateFrom] = useState(initialFrom);
  const [dateTo, setDateTo] = useState(initialTo);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selectedDepartments, setSelectedDepartments] = useState<Set<string>>(new Set());
  const {
    data: departments,
    loading: departmentsLoading,
    error: departmentsError,
    reload: reloadDepartments,
  } = useApi<Department[]>(open ? "/departments/?all=1" : null);
  const departmentKey = departments?.map((department) => department.code).join("|") ?? "";

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

  function datedFilename() {
    const stem = filename.replace(/\.xlsx$/i, "");
    const period =
      dateFrom || dateTo
        ? `${dateFrom || "start"}_${dateTo || todayLocalIsoDate()}`
        : `all-time_${todayLocalIsoDate()}`;
    return `${stem}_${period}.xlsx`;
  }

  async function download() {
    if (!selectedDepartments.size) {
      setError("Выберите хотя бы один отдел для выписки.");
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

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Финансы · Excel"
      title={title}
      description={description}
      className="max-w-2xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Отмена
          </Button>
          <Button onClick={() => void download()} disabled={busy || departmentsLoading || !selectedDepartments.size}>
            <Download className="size-4" /> {busy ? "Формирование…" : "Скачать .xlsx"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="rounded-2xl border border-emerald-100 bg-gradient-to-br from-emerald-50 to-white p-4">
          <div className="flex items-start gap-3">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-emerald-600 text-white shadow-sm">
              <FileSpreadsheet className="size-5" />
            </span>
            <div>
              <div className="text-sm font-bold text-slate-900">{scopeLabel}</div>
              <div className="mt-1 text-xs leading-relaxed text-slate-500">
                {sheetsLabel} Выбрано отделов: {selectedRows.length}.
              </div>
            </div>
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="flex items-center gap-2 text-sm font-bold text-slate-900">
                <Building2 className="size-4 text-emerald-600" /> Отделы в выписке
              </div>
              <p className="mt-1 text-xs text-slate-500">Оставьте только те отделы, которые должны попасть в Excel.</p>
            </div>
            {!!departments?.length && (
              <div className="flex gap-1">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setSelectedDepartments(new Set(departments.map((department) => department.code)))}
                >
                  Все
                </Button>
                <Button type="button" size="sm" variant="ghost" onClick={() => setSelectedDepartments(new Set())}>
                  Снять
                </Button>
              </div>
            )}
          </div>

          {departmentsLoading && !departments && (
            <p className="rounded-xl bg-slate-50 px-3 py-3 text-sm text-slate-500">Загружаем список отделов…</p>
          )}
          {departmentsError && (
            <div className="flex items-center justify-between gap-3 rounded-xl border border-red-100 bg-red-50 px-3 py-2.5 text-sm text-red-800">
              <span>{departmentsError}</span>
              <Button type="button" size="sm" variant="outline" onClick={() => void reloadDepartments()}>
                Повторить
              </Button>
            </div>
          )}
          {!!departments?.length && (
            <div className="grid gap-2 sm:grid-cols-2">
              {departments.map((department) => {
                const selected = selectedDepartments.has(department.code);
                return (
                  <button
                    key={department.code}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => toggleDepartment(department.code)}
                    className={`flex min-h-12 items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition ${
                      selected
                        ? "border-emerald-300 bg-emerald-50 text-emerald-950 shadow-sm"
                        : "border-slate-200 bg-slate-50/60 text-slate-500 hover:border-slate-300"
                    }`}
                  >
                    <span className="size-2.5 shrink-0 rounded-full" style={{ backgroundColor: department.color }} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-semibold">{department.name}</span>
                      {!department.is_active && <span className="block text-[10px]">Архивный отдел</span>}
                    </span>
                    <span
                      className={`flex size-5 shrink-0 items-center justify-center rounded-md border ${
                        selected ? "border-emerald-500 bg-emerald-600 text-white" : "border-slate-300 bg-white"
                      }`}
                    >
                      {selected && <Check className="size-3.5" />}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
          {!departmentsLoading && departments?.length === 0 && (
            <p className="rounded-xl bg-amber-50 px-3 py-3 text-sm text-amber-900">
              Нет отделов для формирования выписки.
            </p>
          )}
        </div>

        <div className="grid grid-cols-3 gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              setDateFrom("");
              setDateTo("");
            }}
          >
            Всё время
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              setDateFrom(monthStartLocalIsoDate());
              setDateTo(todayLocalIsoDate());
            }}
          >
            Этот месяц
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              const value = todayLocalIsoDate();
              setDateFrom(value);
              setDateTo(value);
            }}
          >
            Сегодня
          </Button>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-bold text-slate-800">
            <CalendarRange className="size-4 text-blue-600" /> Период выписки
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className="grid gap-1.5 text-sm font-medium">
              С даты
              <Input
                type="date"
                value={dateFrom}
                max={dateTo || undefined}
                onChange={(event) => setDateFrom(event.target.value)}
              />
            </label>
            <label className="grid gap-1.5 text-sm font-medium">
              По дату
              <Input
                type="date"
                value={dateTo}
                min={dateFrom || undefined}
                onChange={(event) => setDateTo(event.target.value)}
              />
            </label>
          </div>
          <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
            Заказы отбираются по дате создания, продажи — по дате отгрузки, оплаты — по дате подтверждения кассой. Лист
            «Долги» показывает текущий остаток выбранных отделов на момент выгрузки.
          </p>
        </div>

        {error && (
          <p className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm font-medium text-[var(--destructive)]">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}
