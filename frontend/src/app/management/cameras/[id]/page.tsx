"use client";
import { use, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import type { Camera, WebhookCall } from "@/lib/types";

const VARS = ["camera_id", "decision", "allowed", "reason", "order_id",
  "plate", "client_name", "bags", "weight_kg", "net_weight_kg"];

export default function CameraDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: cam, reload } = useApi<Camera>(`/cameras/${id}/`);
  const { data: calls, reload: reloadCalls } = useApi<WebhookCall[]>(`/cameras/${id}/calls/`);
  const [tpl, setTpl] = useState<string | null>(null);
  const [simPlate, setSimPlate] = useState("");
  const [simBags, setSimBags] = useState("");
  const [simResult, setSimResult] = useState<unknown>(null);
  const [revealKey, setRevealKey] = useState("");
  const [error, setError] = useState("");

  if (!cam) return <AppShell title="Камера"><p className="text-sm text-[var(--muted-foreground)]">Загрузка…</p></AppShell>;
  const template = tpl ?? cam.response_template;

  async function saveTpl() {
    setError("");
    try { await api.patch(`/cameras/${id}/`, { response_template: template }); reload(); }
    catch (e) { setError(apiError(e)); }
  }
  async function regenerate() {
    try { const { data } = await api.post(`/cameras/${id}/regenerate_key/`); setRevealKey(data.api_key); reload(); }
    catch (e) { setError(apiError(e)); }
  }
  async function simulate() {
    setError("");
    try {
      const { data } = await api.post(`/cameras/${id}/simulate/`, {
        plate: simPlate, bags: simBags ? Number(simBags) : undefined,
      });
      setSimResult(data);
    } catch (e) { setError(apiError(e)); }
  }

  return (
    <AppShell title={cam.name} section="Камеры">
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Параметры</CardTitle>
            <Badge tone={cam.is_active ? "success" : "muted"}>{cam.is_active ? "Активна" : "Отключена"}</Badge>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 text-sm">
            <div className="flex justify-between"><span className="text-[var(--muted-foreground)]">ID</span><span className="font-mono">{cam.camera_id}</span></div>
            <div className="flex justify-between"><span className="text-[var(--muted-foreground)]">Ключ</span>
              <span className="font-mono text-xs">{revealKey || cam.api_key}</span></div>
            <Button size="sm" variant="outline" onClick={regenerate}>Перегенерировать ключ</Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Симулятор вызова</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Input placeholder="Номер машины" value={simPlate} onChange={(e) => setSimPlate(e.target.value)} />
            {cam.kind === "counter" && (
              <Input type="number" placeholder="Мешков" value={simBags} onChange={(e) => setSimBags(e.target.value)} />)}
            <Button size="sm" onClick={simulate} disabled={!simPlate}>Симулировать</Button>
            {simResult != null && (
              <pre className="overflow-x-auto rounded-md border bg-[var(--muted)] p-3 text-xs">
                {JSON.stringify(simResult, null, 2)}
              </pre>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="mt-6">
        <CardHeader><CardTitle>Шаблон ответа</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-xs text-[var(--muted-foreground)]">
            Переменные: {VARS.map((v) => <code key={v} className="mr-1 rounded bg-[var(--muted)] px-1">{`{{${v}}}`}</code>)}
          </p>
          <textarea value={template} rows={4}
            onChange={(e) => setTpl(e.target.value)}
            className="rounded-md border bg-transparent px-3 py-2 font-mono text-xs outline-none focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/40" />
          {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
          <div className="flex justify-end"><Button size="sm" onClick={saveTpl}>Сохранить шаблон</Button></div>
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>Журнал вызовов</CardTitle>
          <Button size="sm" variant="ghost" onClick={() => reloadCalls()}>Обновить</Button>
        </CardHeader>
        <CardContent>
          <Table>
            <THead><TR><TH>Время</TH><TH>Номер</TH><TH>Решение</TH><TH>Заказ</TH><TH>Причина</TH></TR></THead>
            <TBody>
              {(calls ?? []).map((c) => (
                <TR key={c.id}>
                  <TD className="whitespace-nowrap text-[var(--muted-foreground)]">{new Date(c.created_at).toLocaleString("ru-RU")}</TD>
                  <TD className="tabular-nums">{c.plate}</TD>
                  <TD><Badge tone={c.decision === "allow" ? "success" : "destructive"}>{c.decision}</Badge></TD>
                  <TD>{c.matched_order ? `#${c.matched_order}` : "—"}</TD>
                  <TD className="text-[var(--muted-foreground)]">{c.reason || "—"}</TD>
                </TR>
              ))}
              {(calls ?? []).length === 0 && (
                <TR><TD colSpan={5} className="py-4 text-center text-[var(--muted-foreground)]">Вызовов пока нет.</TD></TR>)}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </AppShell>
  );
}
