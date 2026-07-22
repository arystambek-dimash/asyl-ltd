"use client";

import { useEffect, useState } from "react";
import { CalendarRange, Download, FileSpreadsheet } from "lucide-react";
import { api, apiError } from "@/lib/api";
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

function today() {
  return new Date().toLocaleDateString("en-CA");
}

export function StatementExportModal({
  open, onClose, endpoint, filename, title, description, scopeLabel, sheetsLabel,
  initialFrom = "", initialTo = "",
}: Props) {
  const [dateFrom, setDateFrom] = useState(initialFrom);
  const [dateTo, setDateTo] = useState(initialTo);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setDateFrom(initialFrom);
    setDateTo(initialTo);
    setError("");
  }, [open, initialFrom, initialTo]);

  async function download() {
    setBusy(true);
    setError("");
    try {
      const response = await api.get(endpoint, {
        params: {
          ...(dateFrom ? { date_from: dateFrom } : {}),
          ...(dateTo ? { date_to: dateTo } : {}),
        },
        responseType: "blob",
      });
      const url = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
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
      className="max-w-lg"
      footer={(
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Отмена</Button>
          <Button onClick={() => void download()} disabled={busy}>
            <Download className="size-4" /> {busy ? "Формирование…" : "Скачать .xlsx"}
          </Button>
        </>
      )}
    >
      <div className="space-y-4">
        <div className="rounded-2xl border border-emerald-100 bg-gradient-to-br from-emerald-50 to-white p-4">
          <div className="flex items-start gap-3">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-emerald-600 text-white shadow-sm">
              <FileSpreadsheet className="size-5" />
            </span>
            <div>
              <div className="text-sm font-bold text-slate-900">{scopeLabel}</div>
              <div className="mt-1 text-xs leading-relaxed text-slate-500">{sheetsLabel}</div>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2">
          <Button type="button" variant="outline" onClick={() => { setDateFrom(""); setDateTo(""); }}>
            Всё время
          </Button>
          <Button type="button" variant="outline" onClick={() => {
            const now = new Date();
            setDateFrom(new Date(now.getFullYear(), now.getMonth(), 1).toLocaleDateString("en-CA"));
            setDateTo(today());
          }}>
            Этот месяц
          </Button>
          <Button type="button" variant="outline" onClick={() => { const value = today(); setDateFrom(value); setDateTo(value); }}>
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
              <Input type="date" value={dateFrom} max={dateTo || undefined}
                onChange={(event) => setDateFrom(event.target.value)} />
            </label>
            <label className="grid gap-1.5 text-sm font-medium">
              По дату
              <Input type="date" value={dateTo} min={dateFrom || undefined}
                onChange={(event) => setDateTo(event.target.value)} />
            </label>
          </div>
        </div>

        {error && (
          <p className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm font-medium text-red-600">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}
