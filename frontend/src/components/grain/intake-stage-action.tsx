"use client";
import { useState } from "react";
import { TrainFront } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { formatKg } from "@/lib/grain";
import { useAuth } from "@/store/auth";
import type { GrainWagon } from "@/lib/types";

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

  if (wagon.workflow !== "simple") return null;
  let body: React.ReactNode = null;
  if (["arrived", "at_silo"].includes(wagon.status) && can(me, "grain.weigh")) {
    body = <WagonScalePending />;
  } else if (wagon.status === "weight_discrepancy" && can(me, "grain.inventory")) {
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
