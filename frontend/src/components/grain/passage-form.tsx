"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, apiError } from "@/lib/api";
import type { GrainWagon, VehiclePlateCandidate } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { formatDateTime } from "@/lib/utils";
import { ArrowRight } from "lucide-react";
import { useRef, useState } from "react";

function ocrConfidenceLabel(value: VehiclePlateCandidate["ocr_confidence"]) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${Math.round(numeric * 100)}%` : "—";
}

function candidateDetails(candidate: VehiclePlateCandidate) {
  return (
    <>
      Камера {candidate.camera} · {candidate.source} · {formatDateTime(candidate.detected_at)} · OCR{" "}
      {ocrConfidenceLabel(candidate.ocr_confidence)}
    </>
  );
}

function hasApiErrorCode(cause: unknown, expectedCode: string) {
  const code = (cause as { response?: { data?: { code?: unknown } } }).response?.data?.code;
  return code === expectedCode;
}

export function PassageForm({ onDone, onCancel }: { onDone: (wagon: GrainWagon) => void; onCancel: () => void }) {
  const [number, setNumber] = useState("");
  const [cargo, setCargo] = useState("Отруби");
  const [note, setNote] = useState("");
  const [selectedCandidate, setSelectedCandidate] = useState<VehiclePlateCandidate | null>(null);
  const selectedCandidateRef = useRef<VehiclePlateCandidate | null>(null);
  const [selectedCandidateExpired, setSelectedCandidateExpired] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const {
    data: plateCandidates,
    loading: candidatesLoading,
    error: candidatesError,
    reload: reloadCandidates,
  } = useApi<VehiclePlateCandidate[]>("/grain/passages/vehicle-plate-candidates/");
  useVisiblePolling(reloadCandidates, 10_000);
  const visibleCandidates = (plateCandidates ?? [])
    .filter((candidate) => candidate.event_id !== selectedCandidate?.event_id)
    .slice(0, 5);

  function selectCandidate(candidate: VehiclePlateCandidate) {
    if (busy) return;
    setNumber(candidate.vehicle_number);
    selectedCandidateRef.current = candidate;
    setSelectedCandidate(candidate);
    setSelectedCandidateExpired(false);
    setError("");
  }

  function switchToManualNumber() {
    if (busy) return;
    setNumber("");
    selectedCandidateRef.current = null;
    setSelectedCandidate(null);
    setSelectedCandidateExpired(false);
    setError("");
  }

  async function submit() {
    if (busy || selectedCandidateExpired) return;
    const submittedCandidate = selectedCandidateRef.current;
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post<GrainWagon>("/grain/passages/", {
        number,
        cargo_name: cargo,
        note,
        ...(submittedCandidate ? { vehicle_plate_event_id: submittedCandidate.event_id } : {}),
      });
      onDone(data);
    } catch (cause) {
      if (
        hasApiErrorCode(cause, "vehicle_plate_event_unavailable") &&
        submittedCandidate !== null &&
        selectedCandidateRef.current?.event_id === submittedCandidate.event_id
      ) {
        setSelectedCandidateExpired(true);
        setError("Выбранный номер больше недоступен. Выберите другой номер или перейдите на ручной ввод.");
      } else {
        setError(apiError(cause));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-[var(--border)] bg-[var(--muted)] px-4 py-3">
        <p className="text-sm font-semibold">Машина заезжает пустой</p>
        <p className="mt-1 text-xs leading-relaxed text-[var(--muted-foreground)]">
          Взвесьте её на въезде, затем загрузите и взвесьте на выезде. Вывезенный вес система посчитает сама — заранее
          указывать его не нужно.
        </p>
      </div>
      <div>
        <Label htmlFor="passage-cargo">Что вывозят *</Label>
        <Input
          id="passage-cargo"
          value={cargo}
          onChange={(event) => setCargo(event.target.value)}
          placeholder="Отруби"
        />
      </div>
      <div>
        <Label htmlFor="passage-number">Номер машины</Label>
        <Input
          id="passage-number"
          value={number}
          onChange={(event) => setNumber(event.target.value)}
          readOnly={selectedCandidate !== null}
          placeholder="123 ABC 02"
        />
        {selectedCandidate && (
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">
            Номер закреплён за выбранным событием камеры. Для изменения перейдите на ручной ввод.
          </p>
        )}
      </div>
      {(selectedCandidate || candidatesLoading || candidatesError || visibleCandidates.length > 0) && (
        <section aria-label="Распознанные номера" className="rounded-xl border bg-[var(--muted)]/40 p-3">
          <div className="flex items-baseline justify-between gap-3">
            <div>
              <p className="text-sm font-semibold">Распознанные номера</p>
              <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                Выберите машину явно; ручной ввод включается отдельной кнопкой.
              </p>
            </div>
            {candidatesLoading && <span className="text-xs text-[var(--muted-foreground)]">Обновление…</span>}
          </div>
          <div className="mt-2 space-y-2">
            {selectedCandidate && (
              <div
                className={
                  selectedCandidateExpired
                    ? "rounded-lg border border-amber-300 bg-amber-50 px-3 py-2"
                    : "rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2"
                }
              >
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                  <div className="min-w-0">
                    <p className="font-semibold tabular-nums">
                      {selectedCandidate.vehicle_number} {selectedCandidateExpired ? "· недоступен" : "· выбран"}
                    </p>
                    <p className="text-[11px] text-[var(--muted-foreground)]">{candidateDetails(selectedCandidate)}</p>
                  </div>
                  <Button size="sm" variant="outline" disabled={busy} onClick={switchToManualNumber}>
                    Перейти на ручной ввод
                  </Button>
                </div>
              </div>
            )}
            {candidatesError ? (
              <p className="text-xs text-[var(--destructive)]">{candidatesError}</p>
            ) : (
              visibleCandidates.map((candidate) => {
                return (
                  <div
                    key={candidate.event_id}
                    className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-lg border bg-[var(--card)] px-3 py-2"
                  >
                    <div className="min-w-0">
                      <p className="font-semibold tabular-nums">{candidate.vehicle_number}</p>
                      <p className="text-[11px] text-[var(--muted-foreground)]">{candidateDetails(candidate)}</p>
                    </div>
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => selectCandidate(candidate)}>
                      Использовать
                    </Button>
                  </div>
                );
              })
            )}
          </div>
        </section>
      )}
      <div>
        <Label htmlFor="passage-note">Примечание</Label>
        <Input
          id="passage-note"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Кому отгружаем, номер заявки"
        />
      </div>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel} disabled={busy}>
          Отмена
        </Button>
        <Button disabled={busy || !cargo.trim() || selectedCandidateExpired} onClick={() => void submit()}>
          {busy ? "Оформление…" : "Оформить вывоз"} <ArrowRight className="size-4" />
        </Button>
      </div>
    </div>
  );
}
