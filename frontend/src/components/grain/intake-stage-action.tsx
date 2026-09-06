"use client";
import { useState } from "react";
import { TrainFront } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { formatKg } from "@/lib/grain";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import type { GrainWagon, GrainSilo, GrainSupply } from "@/lib/types";

function WagonScalePending() {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
      <TrainFront className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div>
        <p className="font-semibold">Вагонные весы пока не подключены</p>
        <p className="mt-1 text-amber-900/80">
          Получение веса для прихода станет доступно после подключения отдельного оборудования. Весы машин вывоза здесь
          не используются.
        </p>
      </div>
    </div>
  );
}

export function IntakeStageAction({
  wagon,
  onChanged,
  onBusyChange,
}: {
  wagon: GrainWagon;
  onChanged: () => void;
  onBusyChange: (busy: boolean) => void;
}) {
  const { me } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  const [decision, setDecision] = useState("accepted");
  const [moisture, setMoisture] = useState("");
  const [impurity, setImpurity] = useState("");
  const [labNote, setLabNote] = useState("");
  const [siloId, setSiloId] = useState("");
  const [supplyId, setSupplyId] = useState("");
  const needSilos = ["unloading_allowed", "quarantine", "insufficient_capacity"].includes(wagon.status);
  const { data: silos } = useApi<GrainSilo[]>(needSilos ? `/grain/wagons/${wagon.id}/suggest-silos/` : null);
  const { data: supplies } = useApi<GrainSupply[]>(
    wagon.status === "waiting_for_approval" ? "/grain/supplies/?status=expected" : null,
  );

  const selectedSiloId = siloId || (silos?.[0] ? String(silos[0].id) : "");

  async function act(path: string, body: Record<string, unknown> = {}) {
    setBusy(true);
    onBusyChange(true);
    setError("");
    try {
      await api.post(`/grain/wagons/${wagon.id}/${path}/`, body);
      onChanged();
    } catch (cause) {
      setError(apiError(cause));
      onChanged();
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  }

  let body: React.ReactNode = null;
  if (wagon.workflow === "simple" && ["arrived", "at_silo"].includes(wagon.status) && can(me, "grain.weigh")) {
    body = <WagonScalePending />;
  } else if (wagon.workflow === "simple" && wagon.status === "weight_discrepancy" && can(me, "grain.inventory")) {
    body = (
      <>
        <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-950">
          Фактическое нетто {formatKg(wagon.net_weight_kg)} отличается от ожидаемого{" "}
          {formatKg(wagon.expected_weight_kg)}
          {wagon.weight_difference_percent != null ? ` на ${wagon.weight_difference_percent}%` : ""}.
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Причина подтверждения</Label>
          <Input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="номер акта или пояснение"
          />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void act("resolve-simple-discrepancy", { action: "reweigh" })}
          >
            Повторно взвесить
          </Button>
          <Button
            disabled={busy || !reason}
            onClick={() => void act("resolve-simple-discrepancy", { action: "confirm", reason })}
          >
            Подтвердить фактическое нетто
          </Button>
        </div>
      </>
    );
  } else if (wagon.status === "waiting_for_approval" && can(me, "grain.dispatch")) {
    body = (
      <>
        <div className="flex flex-col gap-1.5">
          <Label>Привязать к поставке</Label>
          <Select value={supplyId} onChange={(e) => setSupplyId(e.target.value)}>
            <option value="">Без поставки</option>
            {(supplies ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                #{item.id} · {item.supplier} · {item.culture}
              </option>
            ))}
          </Select>
        </div>
        <Button disabled={busy} onClick={() => void act("approve", { supply: supplyId || null })}>
          Подтвердить вагон
        </Button>
      </>
    );
  } else if (wagon.status === "arrived" && can(me, "grain.weigh")) {
    body = <WagonScalePending />;
  } else if (wagon.status === "lab_pending" && can(me, "grain.lab")) {
    body = (
      <>
        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1.5">
            <Label>Влажность, %</Label>
            <Input type="number" value={moisture} onChange={(e) => setMoisture(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Сорность, %</Label>
            <Input type="number" value={impurity} onChange={(e) => setImpurity(e.target.value)} />
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Решение</Label>
          <Select value={decision} onChange={(e) => setDecision(e.target.value)}>
            <option value="accepted">Принято</option>
            <option value="accepted_with_restrictions">Принято с ограничениями</option>
            <option value="rejected">Отклонено</option>
            <option value="quarantine">Карантин</option>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Комментарий лаборатории</Label>
          <Input value={labNote} onChange={(e) => setLabNote(e.target.value)} />
        </div>
        <Button
          disabled={busy}
          onClick={() =>
            void act("lab", {
              decision,
              moisture: moisture || null,
              impurity: impurity || null,
              note: labNote,
            })
          }
        >
          Сохранить решение
        </Button>
      </>
    );
  } else if (needSilos && can(me, "grain.dispatch")) {
    body = (
      <>
        <div className="flex flex-col gap-1.5">
          <Label>Подходящие силосы</Label>
          <Select value={selectedSiloId} onChange={(e) => setSiloId(e.target.value)}>
            <option value="">Выберите силос</option>
            {(silos ?? []).map((silo) => (
              <option key={silo.id} value={silo.id}>
                {silo.is_default_route ? "★ " : ""}
                {silo.name} · свободно {formatKg(silo.free_capacity_kg)}
              </option>
            ))}
          </Select>
        </div>
        {(silos ?? []).length === 0 && (
          <p className="text-sm text-[var(--warning)]">
            Подходящих силосов нет: проверьте культуру, класс и свободное место.
          </p>
        )}
        {silos?.[0]?.is_default_route && (
          <p className="text-xs text-[var(--muted-foreground)]">
            Основной маршрут для этого типа зерна выбран автоматически: «{silos[0].name}».
          </p>
        )}
        <Button
          disabled={busy || !selectedSiloId}
          onClick={() => void act("assign-silo", { silo: Number(selectedSiloId) })}
        >
          Назначить силос
        </Button>
      </>
    );
  } else if (wagon.status === "silo_assigned" && can(me, "grain.unload")) {
    body = (
      <Button disabled={busy} onClick={() => void act("start-unloading")}>
        Начать разгрузку в «{wagon.assigned_silo_name}»
      </Button>
    );
  } else if (wagon.status === "unloading" && can(me, "grain.unload")) {
    body = (
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => void act("pause-unloading", { paused: !wagon.unloading_paused })}
        >
          {wagon.unloading_paused ? "Продолжить" : "Приостановить"}
        </Button>
        <Button disabled={busy} onClick={() => void act("finish-unloading")}>
          Завершить разгрузку
        </Button>
      </div>
    );
  } else if (
    (wagon.status === "unloading_completed" || wagon.status === "reweighing_required") &&
    can(me, "grain.weigh")
  ) {
    body = <WagonScalePending />;
  } else if (wagon.status === "weight_discrepancy" && can(me, "grain.inventory")) {
    body = (
      <>
        <div className="flex flex-col gap-1.5">
          <Label>Обоснование подтверждения</Label>
          <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="акт сверки, номер документа" />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void act("resolve-discrepancy", { action: "reweigh" })}
          >
            Перевесить
          </Button>
          <Button
            disabled={busy || !reason}
            onClick={() => void act("resolve-discrepancy", { action: "confirm", reason })}
          >
            Подтвердить фактический вес
          </Button>
        </div>
      </>
    );
  } else if (wagon.status === "tare_weighed" && can(me, "grain.inventory")) {
    body = (
      <Button disabled={busy} onClick={() => void act("inventory")}>
        Оприходовать {formatKg(wagon.net_weight_kg)} в «{wagon.assigned_silo_name}»
      </Button>
    );
  } else if (wagon.status === "exit_allowed" && can(me, "grain.exit")) {
    body = (
      <Button disabled={busy} onClick={() => void act("exit")}>
        Выпустить вагон
      </Button>
    );
  }

  if (!body) return null;
  return (
    <Card>
      <CardHeader className="p-4 pb-2">
        <CardTitle>Действие сейчас</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 p-4 pt-2">
        {body}
        {error && (
          <p role="alert" className="text-sm text-[var(--destructive)]">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
