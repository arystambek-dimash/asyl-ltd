"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Camera, Save, ScanLine, Trash2, VideoOff } from "lucide-react";
import { CameraStream } from "@/components/camera-stream";
import { playableCameras, type CameraFeed } from "@/components/camera-wall";
import { Button } from "@/components/ui/button";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { Field } from "@/components/ui/field";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import type {
  ShippingTransportCameraSettings,
  ShippingTransportRecognition,
  TransportRecognitionModel,
} from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { formatDateTime } from "@/lib/utils";

type Draft = { number_camera: string; recognition_model: TransportRecognitionModel | "" };
type Action = "save" | "delete" | "recognize";

/** A different conveyor gets its own drafts and request lifetime. */
export function ShippingTransportCamera({ conveyorCamera }: { conveyorCamera: string }) {
  return <TransportCameraForm key={conveyorCamera} conveyorCamera={conveyorCamera} />;
}

function NumberCameraPreview({ source, name }: { source: string; name: string }) {
  const [online, setOnline] = useState(false);
  return (
    <div className="relative aspect-video max-h-80 overflow-hidden rounded-lg bg-[#141416]">
      <CameraStream
        src={`${source}main`}
        onStateChange={setOnline}
        className="absolute inset-0 size-full object-contain"
      />
      {!online && (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 text-white/65">
          <VideoOff className="size-6" />
          <p className="text-sm">Ожидаем изображение</p>
        </div>
      )}
      <span className="absolute bottom-3 left-3 max-w-[calc(100%-24px)] truncate rounded-md bg-black/60 px-2 py-1 text-xs text-white">
        {name} · основной поток
      </span>
    </div>
  );
}

function TransportCameraForm({ conveyorCamera }: { conveyorCamera: string }) {
  const id = useId();
  const url = `/cameras/${encodeURIComponent(conveyorCamera)}/transport-camera/`;
  const settings = useApi<ShippingTransportCameraSettings>(url);
  const inventory = useApi<CameraFeed[]>("/cameras/");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [action, setAction] = useState<Action | null>(null);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [result, setResult] = useState<ShippingTransportRecognition | null>(null);
  const inFlight = useRef(false);
  const generation = useRef(0);
  const testController = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      generation.current += 1;
      testController.current?.abort();
    },
    [],
  );

  const saved = settings.data;
  const selection: Draft = draft ?? {
    number_camera: saved?.number_camera ?? "",
    recognition_model: saved?.recognition_model ?? "",
  };
  const dirty =
    selection.number_camera !== (saved?.number_camera ?? "") ||
    selection.recognition_model !== (saved?.recognition_model ?? "");
  const cameras = playableCameras(inventory.data).filter(
    (camera) => /^cam[1-9]\d*$/.test(camera.src) && camera.src !== conveyorCamera,
  );
  const selectedCamera = cameras.find((camera) => camera.src === selection.number_camera);
  const validSelection = !!selectedCamera && !!selection.recognition_model;
  const configured = !!saved?.number_camera && !!saved.recognition_model;
  const ready =
    !!saved && !!inventory.data && !settings.error && !inventory.error && !settings.loading && !inventory.loading;

  function changeDraft(next: Draft) {
    if (inFlight.current) return;
    setDraft(next);
    setActionError("");
    setNotice("");
    setResult(null);
  }

  async function perform(nextAction: Action) {
    if (inFlight.current || !ready || !saved) return;
    if (nextAction === "save" && (!validSelection || !dirty)) return;
    if (nextAction === "recognize" && (!configured || dirty)) return;
    if (nextAction === "delete" && !configured) return;
    inFlight.current = true;
    setAction(nextAction);
    setActionError("");
    setNotice("");
    setResult(null);
    const requestGeneration = generation.current;
    const current = () => requestGeneration === generation.current;
    try {
      if (nextAction === "save") {
        const { data } = await api.put<ShippingTransportCameraSettings>(url, selection);
        if (!current()) return;
        settings.setData(data);
        setDraft(null);
        setNotice("Связь сохранена");
      } else if (nextAction === "delete") {
        await api.delete(url);
        if (!current()) return;
        settings.setData({
          conveyor_camera: conveyorCamera,
          number_camera: null,
          recognition_model: null,
          updated_at: null,
        });
        setDraft(null);
        setNotice("Связь удалена");
      } else {
        const controller = new AbortController();
        testController.current = controller;
        const { data } = await api.post<ShippingTransportRecognition>(
          `${url}recognize/`,
          {},
          { signal: controller.signal },
        );
        if (!current() || controller.signal.aborted) return;
        if (
          data.conveyor_camera !== conveyorCamera ||
          data.number_camera !== saved.number_camera ||
          data.recognition_model !== saved.recognition_model
        ) {
          setActionError("Связь камеры изменилась. Обновите настройки и повторите проверку.");
          return;
        }
        setResult(data);
      }
    } catch (cause) {
      if (current()) setActionError(apiError(cause));
    } finally {
      if (current()) {
        inFlight.current = false;
        testController.current = null;
        setAction(null);
      }
    }
  }

  async function reload() {
    if (inFlight.current) return;
    setResult(null);
    setActionError("");
    await Promise.all([settings.reload(), inventory.reload()]);
  }

  if (!ready)
    return (
      <DataGate
        loading={settings.loading || inventory.loading}
        error={settings.error || inventory.error}
        onRetry={() => void reload()}
      />
    );

  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-base font-semibold">Распознавание транспорта у конвейера</h3>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          Выберите камеру, которая видит номер машины или вагона.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Камера номера" htmlFor={`${id}-camera`}>
          <Select
            id={`${id}-camera`}
            value={selection.number_camera}
            disabled={!!action}
            onChange={(event) => changeDraft({ ...selection, number_camera: event.target.value })}
          >
            <option value="">Выберите камеру</option>
            {selection.number_camera && !selectedCamera && (
              <option value={selection.number_camera} disabled>
                {selection.number_camera} · недоступна
              </option>
            )}
            {cameras.map((camera) => (
              <option key={camera.id} value={camera.src}>
                {camera.zone || camera.name} · {camera.src}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Модель распознавания" htmlFor={`${id}-model`}>
          <Select
            id={`${id}-model`}
            value={selection.recognition_model}
            disabled={!!action}
            onChange={(event) => {
              const value = event.target.value;
              if (value === "" || value === "vehicle_number" || value === "wagon_number")
                changeDraft({ ...selection, recognition_model: value });
            }}
          >
            <option value="">Выберите модель</option>
            <option value="vehicle_number">Госномера грузовиков</option>
            <option value="wagon_number">Номера вагонов</option>
          </Select>
        </Field>
      </div>
      {!cameras.length && (
        <p className="text-sm text-[var(--muted-foreground)]">
          Другие камеры camN не найдены. Обновите список после подключения камеры.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button disabled={!!action || !dirty || !validSelection} onClick={() => void perform("save")}>
          <Save className="size-4" />
          {action === "save" ? "Сохранение…" : "Сохранить связь"}
        </Button>
        {configured && (
          <Button variant="outline" disabled={!!action} onClick={() => void perform("delete")}>
            <Trash2 className="size-4" />
            {action === "delete" ? "Удаление…" : "Удалить связь"}
          </Button>
        )}
        <Button variant="ghost" disabled={!!action} onClick={() => void reload()}>
          Обновить
        </Button>
        {dirty && <span className="text-xs text-[var(--muted-foreground)]">Изменения не сохранены</span>}
      </div>
      {notice && (
        <p role="status" className="text-sm text-[var(--success)]">
          {notice}
        </p>
      )}
      {actionError && <ErrorAlert message={actionError} />}

      {selection.number_camera ? (
        <NumberCameraPreview
          key={selection.number_camera}
          source={selection.number_camera}
          name={selectedCamera?.zone || selectedCamera?.name || selection.number_camera}
        />
      ) : (
        <div className="flex min-h-40 flex-col items-center justify-center gap-2 rounded-lg border border-dashed text-[var(--muted-foreground)]">
          <Camera className="size-6" />
          <p className="text-sm">Здесь появится изображение выбранной камеры</p>
        </div>
      )}

      <div className="space-y-3 rounded-lg border p-4">
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="outline"
            disabled={!!action || !configured || dirty}
            onClick={() => void perform("recognize")}
          >
            <ScanLine className="size-4" />
            {action === "recognize" ? "Распознаём…" : "Проверить распознавание"}
          </Button>
          <p className="text-sm text-[var(--muted-foreground)]">
            {dirty ? "Сначала сохраните связь камеры." : "Проверка номера не запускает погрузку."}
          </p>
        </div>
        {result && (
          <div role="status" className="space-y-1">
            <p className="text-lg font-semibold tabular-nums">{result.number ?? "Номер не распознан"}</p>
            <p className="text-xs text-[var(--muted-foreground)]">{formatDateTime(result.observed_at)}</p>
          </div>
        )}
      </div>
    </div>
  );
}
