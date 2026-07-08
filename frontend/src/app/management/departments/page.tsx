"use client";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { Check } from "lucide-react";

interface DepartmentRow { id: number; code: string; name: string; }

// Пояснение, за что отвечает каждый отдел (коды фиксированы).
const DEPT_HINTS: Record<string, string> = {
  main: "Основной отдел: поставки, оплата, доставка.",
  field: "Выездной отдел: менеджеры собирают заявки и принимают оплату у клиента.",
};

function DepartmentsInner() {
  const { data: departments, reload } = useApi<DepartmentRow[]>("/departments/");
  const { loadMe } = useAuth();
  const [names, setNames] = useState<Record<number, string>>({});
  const [savedId, setSavedId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!departments) return;
    setNames(Object.fromEntries(departments.map((d) => [d.id, d.name])));
  }, [departments]);

  async function save(d: DepartmentRow) {
    const name = (names[d.id] ?? "").trim();
    if (!name || name === d.name) return;
    setBusyId(d.id); setError(""); setSavedId(null);
    try {
      await api.patch(`/departments/${d.id}/`, { name });
      setSavedId(d.id);
      await reload();
      // Названия приходят из /auth/me/ — обновляем, чтобы меню и бейджи
      // сменились сразу, без перелогина.
      await loadMe();
    } catch (e) { setError(apiError(e)); } finally { setBusyId(null); }
  }

  return (
    <AppShell title="Отделы" section="Управление"
      description="Названия отделов продаж. Меняются везде сразу: меню, фильтры, бейджи и формы.">
      <div className="grid max-w-2xl grid-cols-1 gap-4">
        {(departments ?? []).map((d) => (
          <Card key={d.id}>
            <CardHeader className="flex-row items-center justify-between gap-3 pb-3">
              <CardTitle className="text-base">{names[d.id] ?? d.name}</CardTitle>
              <Badge tone={d.code === "field" ? "primary" : "muted"}>
                {d.code === "field" ? "выездной" : "основной"}
              </Badge>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <p className="text-xs text-[var(--muted-foreground)]">
                {DEPT_HINTS[d.code] ?? ""}
              </p>
              <div className="flex gap-2">
                <Input value={names[d.id] ?? ""} maxLength={100}
                  onChange={(e) => setNames((n) => ({ ...n, [d.id]: e.target.value }))}
                  onKeyDown={(e) => { if (e.key === "Enter") save(d); }} />
                <Button size="sm" className="shrink-0 self-center"
                  disabled={busyId === d.id || !(names[d.id] ?? "").trim()
                    || (names[d.id] ?? "").trim() === d.name}
                  onClick={() => save(d)}>
                  {busyId === d.id ? "Сохранение…"
                    : savedId === d.id ? <><Check className="size-4" /> Сохранено</>
                    : "Сохранить"}
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
        {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      </div>
    </AppShell>
  );
}

export default function DepartmentsPage() {
  return (
    <RequirePerm perm="rbac.manage" superuserOnly title="Отделы">
      <DepartmentsInner />
    </RequirePerm>
  );
}
