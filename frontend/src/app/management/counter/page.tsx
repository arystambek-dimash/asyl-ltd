"use client";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { LicensePlateInput } from "@/components/ui/license-plate-input";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import type { Camera } from "@/lib/types";

export default function CounterPage() {
  const { data: cameras } = useApi<Camera[]>("/cameras/");
  const { me } = useAuth();
  const canManage = can(me, "cameras.manage");
  const counters = (cameras ?? []).filter((c) => c.kind === "counter" && c.status === "active");

  const [camId, setCamId] = useState<number | null>(null);
  const [bags, setBags] = useState(0);
  const [plate, setPlate] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (camId == null && counters.length) setCamId(counters[0].id);
  }, [counters, camId]);

  useEffect(() => {
    if (camId == null) return;
    let alive = true;
    const tick = async () => {
      try { const { data } = await api.get(`/count/${camId}/`); if (alive) setBags(data.bags); }
      catch { /* ignore poll errors */ }
    };
    tick();
    const t = setInterval(tick, 1500);
    return () => { alive = false; clearInterval(t); };
  }, [camId]);

  async function close() {
    if (camId == null) return;
    setBusy(true); setError(""); setMsg("");
    try {
      const { data } = await api.post(`/count/${camId}/close/`, { plate });
      setMsg(`${data.bags} мешков записано в заказ #${data.order_id}`);
      setPlate(""); setBags(0);
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  return (
    <AppShell title="Счётчик мешков" section="Управление"
      description="Живой счёт мешков с камеры-счётчика. Введите номер машины и завершите сессию — итог уйдёт в заказ.">
      {counters.length === 0 ? (
        <Card><CardContent className="py-10 text-center text-sm text-[var(--muted-foreground)]">
          Нет активных камер-счётчиков. Добавьте камеру типа «Счётчик загрузки».
        </CardContent></Card>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
          <Card>
            <CardHeader><CardTitle>Загружено мешков</CardTitle></CardHeader>
            <CardContent className="flex flex-col items-center gap-2 py-10">
              {counters.length > 1 && (
                <Select className="mb-4 max-w-xs" value={camId ?? ""}
                  onChange={(e) => setCamId(Number(e.target.value))}>
                  {counters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </Select>
              )}
              <div className="text-7xl font-bold tabular-nums">{bags}</div>
              <div className="text-sm text-[var(--muted-foreground)]">мешков в текущей сессии</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Завершить сессию</CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-4">
              <div className="grid gap-2">
                <Label>Номер машины</Label>
                <LicensePlateInput value={plate} onChange={setPlate} />
              </div>
              {msg && <p className="rounded-md bg-[var(--success)]/12 px-3 py-2 text-sm text-[var(--success)]">{msg}</p>}
              {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
              {canManage && (
                <Button disabled={busy || plate.replace(/\D/g, "").length < 1}
                  onClick={close}>
                  {busy ? "Сохранение…" : "Закончить сессию"}
                </Button>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </AppShell>
  );
}
