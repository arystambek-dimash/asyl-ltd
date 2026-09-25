"use client";
import { useEffect, useRef, useState } from "react";
import { LoaderCircle, Scale } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, apiError, apiErrorCode } from "@/lib/api";
import { can } from "@/lib/can";
import { useAuth } from "@/store/auth";
import type { GrainWagon } from "@/lib/types";

type PassageCapturePath = "entry-weight" | "exit-weight";

const CANONICAL_UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const CAPTURE_RESUME_CODES = new Set([
  "passage_capture_busy",
  "passage_capture_in_progress",
  "passage_capture_resume_required",
]);

function isCanonicalUuid(value: unknown): value is string {
  return typeof value === "string" && CANONICAL_UUID_RE.test(value);
}

function latestCaptureForPath(wagon: GrainWagon, path: PassageCapturePath) {
  const action = path === "entry-weight" ? "entry" : "exit";
  return wagon.vehicle_recognition_captures?.find((item) => item.action === action && isCanonicalUuid(item.request_id));
}

function processingCaptureRequestId(wagon: GrainWagon, path: PassageCapturePath) {
  const capture = latestCaptureForPath(wagon, path);
  return capture?.status === "processing" ? capture.request_id : null;
}

function captureStorageKey(userId: number, wagonId: number, path: PassageCapturePath) {
  return `asyl:passage-weight-capture:v1:${userId}:${wagonId}:${path}`;
}

/**
 * Ключ идемпотентности живёт в памяти страницы и в sessionStorage, чтобы
 * пережить перезагрузку. Хранилище может быть недоступно (приватный режим) —
 * тогда страницу защищает только память.
 */
function readCaptureKey(keys: Map<string, string>, key: string) {
  const inMemory = keys.get(key);
  if (inMemory) return inMemory;
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function storeCaptureKey(keys: Map<string, string>, key: string, requestId: string) {
  keys.set(key, requestId);
  try {
    sessionStorage.setItem(key, requestId);
  } catch {
    // The in-memory captureKeys map still protects this page instance.
  }
}

function forgetCaptureKey(keys: Map<string, string>, key: string) {
  keys.delete(key);
  try {
    sessionStorage.removeItem(key);
  } catch {
    // Storage can be unavailable in hardened/private browser modes.
  }
}

function ScaleCaptureButton({
  busy,
  label,
  busyLabel,
  onClick,
}: {
  busy: boolean;
  label: string;
  busyLabel: string;
  onClick: () => void;
}) {
  return (
    <Button
      className="h-auto min-h-10 whitespace-normal py-2.5 text-center"
      disabled={busy}
      aria-busy={busy}
      onClick={onClick}
    >
      {busy ? <LoaderCircle className="animate-spin" /> : <Scale />}
      {busy ? busyLabel : label}
    </Button>
  );
}

export function PassageStageAction({
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
  const [captureUncertain, setCaptureUncertain] = useState(false);
  const captureKeys = useRef(new Map<string, string>());
  const passage = wagon.direction === "passage";
  const meId = me?.id;
  const activeCapturePath: PassageCapturePath | null =
    passage && wagon.status === "arrived"
      ? "entry-weight"
      : passage && wagon.status === "at_silo"
        ? "exit-weight"
        : null;
  const serverCapture = activeCapturePath ? latestCaptureForPath(wagon, activeCapturePath) : undefined;
  const serverCaptureRequestId = serverCapture?.request_id ?? null;
  const serverCaptureStatus = serverCapture?.status ?? null;

  function clearCaptureKey(path: PassageCapturePath) {
    if (!meId) return;
    forgetCaptureKey(captureKeys.current, captureStorageKey(meId, wagon.id, path));
  }

  function rememberCaptureKey(path: PassageCapturePath, requestId: string) {
    if (!meId) return;
    storeCaptureKey(captureKeys.current, captureStorageKey(meId, wagon.id, path), requestId);
  }

  function getOrCreateCaptureKey(path: PassageCapturePath) {
    if (!meId) return null;
    const serverRequestId = processingCaptureRequestId(wagon, path);
    if (serverRequestId) {
      rememberCaptureKey(path, serverRequestId);
      return serverRequestId;
    }

    const key = captureStorageKey(meId, wagon.id, path);
    const existing = readCaptureKey(captureKeys.current, key);
    if (isCanonicalUuid(existing)) {
      captureKeys.current.set(key, existing);
      return existing;
    }
    forgetCaptureKey(captureKeys.current, key);
    const requestId = crypto.randomUUID();
    rememberCaptureKey(path, requestId);
    return requestId;
  }

  useEffect(() => {
    if (!meId || !passage) {
      setCaptureUncertain(false);
      return;
    }

    for (const path of ["entry-weight", "exit-weight"] as const) {
      if (path !== activeCapturePath) forgetCaptureKey(captureKeys.current, captureStorageKey(meId, wagon.id, path));
    }
    if (!activeCapturePath) {
      setCaptureUncertain(false);
      return;
    }

    const key = captureStorageKey(meId, wagon.id, activeCapturePath);
    if (serverCaptureRequestId && serverCaptureStatus === "processing") {
      storeCaptureKey(captureKeys.current, key, serverCaptureRequestId);
      setCaptureUncertain(true);
      return;
    }

    const stored = readCaptureKey(captureKeys.current, key);
    if (serverCaptureRequestId === stored && serverCaptureStatus && serverCaptureStatus !== "processing") {
      forgetCaptureKey(captureKeys.current, key);
      setCaptureUncertain(false);
      return;
    }
    if (isCanonicalUuid(stored)) {
      captureKeys.current.set(key, stored);
      setCaptureUncertain(true);
      return;
    }
    forgetCaptureKey(captureKeys.current, key);
    setCaptureUncertain(false);
  }, [activeCapturePath, meId, passage, serverCaptureRequestId, serverCaptureStatus, wagon.id]);

  async function act(path: PassageCapturePath) {
    const idempotencyKey = getOrCreateCaptureKey(path);
    if (!idempotencyKey) return;
    setBusy(true);
    onBusyChange(true);
    setError("");
    try {
      await api.post(`/grain/passages/${wagon.id}/${path}/`, {}, { headers: { "Idempotency-Key": idempotencyKey } });
      clearCaptureKey(path);
      setCaptureUncertain(false);
      onChanged();
    } catch (e) {
      const response = (
        e as {
          response?: {
            status?: number;
            data?: {
              request_id?: unknown;
              retryable?: unknown;
            };
          };
        }
      ).response;
      const responseRequestId = isCanonicalUuid(response?.data?.request_id) ? response.data.request_id : null;
      const responseCode = apiErrorCode(e);
      const retryable = response?.data?.retryable;
      const responseStatus = response?.status;
      const transientResponse =
        !response ||
        typeof responseStatus !== "number" ||
        responseStatus === 408 ||
        responseStatus === 425 ||
        responseStatus === 429 ||
        responseStatus >= 500;
      const resumeResponse = Boolean(responseRequestId && CAPTURE_RESUME_CODES.has(responseCode));
      const retainCaptureKey = retryable === true || (retryable !== false && (transientResponse || resumeResponse));
      if (retainCaptureKey) {
        rememberCaptureKey(path, responseRequestId || idempotencyKey);
      } else {
        clearCaptureKey(path);
      }
      setCaptureUncertain(retainCaptureKey);
      setError(apiError(e));
      // A mutation can commit even when its HTTP response is lost. Reloading
      // prevents an operator from repeating an already completed weighing.
      onChanged();
    } finally {
      setBusy(false);
      onBusyChange(false);
    }
  }

  if (!can(me, "grain.weigh") || !activeCapturePath) return null;
  const entry = activeCapturePath === "entry-weight";
  return (
    <Card>
      <CardHeader className="p-4 pb-2">
        <CardTitle>Действие сейчас</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 p-4 pt-2">
        <p className="rounded-xl border bg-[var(--muted)]/40 p-3 text-sm">
          {entry
            ? `Поставьте пустую машину на весы перед погрузкой «${wagon.cargo_name}». Система сама получит текущий вес.`
            : `После погрузки «${wagon.cargo_name}» поставьте машину на весы. Система получит вес, рассчитает вывезенное нетто и завершит рейс.`}
        </p>
        <ScaleCaptureButton
          busy={busy}
          busyLabel="Фиксирую показание весов…"
          label={
            captureUncertain
              ? "Проверить результат повторно"
              : entry
                ? "Получить вес пустой и отправить на погрузку"
                : "Получить вес гружёной и завершить вывоз"
          }
          onClick={() => void act(activeCapturePath)}
        />
        {captureUncertain && (
          <p className="rounded-lg bg-amber-50 p-2 text-sm text-amber-950">
            Результат ещё не подтверждён. Повтор будет отправлен с тем же идентификатором операции.
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-[var(--destructive)]">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
