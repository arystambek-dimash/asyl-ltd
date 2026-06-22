"use client";
import { useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { Plus } from "lucide-react";
import type { Camera } from "@/lib/types";

const KIND_LABELS: Record<string, string> = {
  entry: "Въезд", counter: "Счётчик", exit: "Выезд",
};

export default function CamerasPage() {
  const { data: cameras, reload } = useApi<Camera[]>("/cameras/");
  const { me } = useAuth();
  const canManage = can(me, "cameras.manage");
  const empty = { name: "", camera_id: "", kind: "entry", response_template: "" };
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(empty);
  const [createdKey, setCreatedKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // привязка обнаруженной камеры
  const [bindCam, setBindCam] = useState<Camera | null>(null);
  const [bindForm, setBindForm] = useState({ name: "", kind: "entry" });
  const [bindKey, setBindKey] = useState("");

  const pending = (cameras ?? []).filter((c) => c.status === "pending");
  const active = (cameras ?? []).filter((c) => c.status !== "pending");

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const { data } = await api.post("/cameras/", form);
      setCreatedKey(data.api_key);
      setForm(empty); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  async function bind(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const { data } = await api.post(`/cameras/${bindCam!.id}/bind/`, bindForm);
      setBindKey(data.api_key); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  return (
    <AppShell title="Камеры" section="Управление"
      description="Камеры поста отгрузки: вебхук по номеру машины, настраиваемый ответ и журнал вызовов."
      actions={canManage ? (
        <Button size="sm" onClick={() => { setError(""); setCreatedKey(""); setOpen(true); }}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Добавить камеру</span>
        </Button>
      ) : undefined}>
      {pending.length > 0 && (
        <Card className="mb-6 border-[var(--warning)]/40">
          <CardContent className="pt-6">
            <div className="mb-3 text-sm font-semibold">
              Обнаруженные камеры <Badge tone="warning">{pending.length}</Badge>
            </div>
            <Table>
              <THead><TR><TH>ID камеры</TH><TH>Последний вызов</TH><TH></TH></TR></THead>
              <TBody>
                {pending.map((c) => (
                  <TR key={c.id}>
                    <TD className="font-mono">{c.camera_id}</TD>
                    <TD className="text-[var(--muted-foreground)]">
                      {c.last_seen ? new Date(c.last_seen).toLocaleString("ru-RU") : "—"}</TD>
                    <TD className="text-right">
                      {canManage && <Button size="sm" onClick={() => {
                        setBindCam(c); setBindForm({ name: c.camera_id, kind: "entry" });
                        setBindKey(""); setError("");
                      }}>Привязать</Button>}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <div className="mb-4">
        <p className="text-sm text-[var(--muted-foreground)]">{active.length} камер</p>
      </div>
      <Card><CardContent className="pt-6">
        <Table>
          <THead><TR><TH>Камера</TH><TH>ID</TH><TH>Тип</TH><TH>Ключ</TH><TH>Статус</TH></TR></THead>
          <TBody>
            {active.map((c) => (
              <TR key={c.id}>
                <TD className="font-medium">
                  <Link href={`/management/cameras/${c.id}`} className="hover:underline">{c.name}</Link>
                </TD>
                <TD className="tabular-nums">{c.camera_id}</TD>
                <TD><Badge tone="muted">{KIND_LABELS[c.kind] ?? "—"}</Badge></TD>
                <TD className="font-mono text-xs text-[var(--muted-foreground)]">{c.api_key}</TD>
                <TD><Badge tone={c.is_active ? "success" : "muted"}>
                  {c.is_active ? "Активна" : "Отключена"}</Badge></TD>
              </TR>
            ))}
            {active.length === 0 && (
              <TR><TD colSpan={5} className="py-4 text-center text-[var(--muted-foreground)]">Камер пока нет.</TD></TR>)}
          </TBody>
        </Table>
      </CardContent></Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Новая камера" className="max-w-lg">
        {createdKey ? (
          <div className="flex flex-col gap-4">
            <p className="text-sm">Камера создана. Сохраните ключ — он показывается один раз:</p>
            <code className="block break-all rounded-md border bg-[var(--muted)] p-3 text-xs">{createdKey}</code>
            <div className="flex justify-end">
              <Button onClick={() => { setOpen(false); setCreatedKey(""); }}>Готово</Button>
            </div>
          </div>
        ) : (
          <form onSubmit={submit} className="flex flex-col gap-4">
            <div className="grid gap-2"><Label>Название</Label>
              <Input value={form.name} required onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
            <div className="grid gap-2"><Label>ID камеры (напр. gate-01)</Label>
              <Input value={form.camera_id} required
                onChange={(e) => setForm({ ...form, camera_id: e.target.value })} /></div>
            <div className="grid gap-2"><Label>Тип</Label>
              <Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                <option value="entry">Въезд</option>
                <option value="counter">Счётчик загрузки</option>
                <option value="exit">Выезд</option>
              </Select></div>
            <div className="grid gap-2"><Label>Шаблон ответа (JSON, необязательно)</Label>
              <textarea value={form.response_template} rows={3}
                placeholder='{"open": {{allowed}}, "order": {{order_id}}}'
                onChange={(e) => setForm({ ...form, response_template: e.target.value })}
                className="rounded-md border bg-transparent px-3 py-2 font-mono text-xs outline-none focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/40" /></div>
            {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
            <div className="flex justify-end gap-2 border-t pt-4">
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>Отмена</Button>
              <Button type="submit" disabled={busy}>{busy ? "Создание…" : "Создать"}</Button>
            </div>
          </form>
        )}
      </Modal>

      <Modal open={!!bindCam} onClose={() => setBindCam(null)}
        title={`Привязать камеру ${bindCam?.camera_id ?? ""}`} className="max-w-lg">
        {bindKey ? (
          <div className="flex flex-col gap-4">
            <p className="text-sm">Камера привязана. Сохраните её ключ — он показывается один раз:</p>
            <code className="block break-all rounded-md border bg-[var(--muted)] p-3 text-xs">{bindKey}</code>
            <div className="flex justify-end">
              <Button onClick={() => { setBindCam(null); setBindKey(""); }}>Готово</Button>
            </div>
          </div>
        ) : (
          <form onSubmit={bind} className="flex flex-col gap-4">
            <div className="grid gap-2"><Label>Название</Label>
              <Input value={bindForm.name} required
                onChange={(e) => setBindForm({ ...bindForm, name: e.target.value })} /></div>
            <div className="grid gap-2"><Label>Тип</Label>
              <Select value={bindForm.kind} onChange={(e) => setBindForm({ ...bindForm, kind: e.target.value })}>
                <option value="entry">Въезд</option>
                <option value="counter">Счётчик загрузки</option>
                <option value="exit">Выезд</option>
              </Select></div>
            {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
            <div className="flex justify-end gap-2 border-t pt-4">
              <Button type="button" variant="outline" onClick={() => setBindCam(null)}>Отмена</Button>
              <Button type="submit" disabled={busy}>{busy ? "Привязка…" : "Привязать"}</Button>
            </div>
          </form>
        )}
      </Modal>
    </AppShell>
  );
}
