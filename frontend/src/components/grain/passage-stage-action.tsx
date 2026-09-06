"use client";
import { useEffect, useRef, useState } from "react";
import { LoaderCircle, Scale } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, apiError } from "@/lib/api";
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

function readStoredCaptureKey(key: string) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStoredCaptureKey(key: string, value: string) {
  try {
    sessionStorage.setItem(key, value);
  } catch {
    // The in-memory fallback in StageAction still protects this page instance.
  }
}

function removeStoredCaptureKey(key: string) {
  try {
    sessionStorage.removeItem(key);
  } catch {
    // Storage can be unavailable in hardened/private browser modes.
  }
}

function ScaleCaptureButton({
  busy,
  label,
  busyLabel = "Получаю вес с весов…",
  onClick,
}: {
  busy: boolean;
  label: string;
  busyLabel?: string;
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
    const key = captureStorageKey(meId, wagon.id, path);
    captureKeys.current.delete(key);
    removeStoredCaptureKey(key);
  }

  function rememberCaptureKey(path: PassageCapturePath, requestId: string) {
    if (!meId) return;
    const key = captureStorageKey(meId, wagon.id, path);
    captureKeys.current.set(key, requestId);
    writeStoredCaptureKey(key, requestId);
  }

  function getOrCreateCaptureKey(path: PassageCapturePath) {
    if (!meId) return null;
    const serverRequestId = processingCaptureRequestId(wagon, path);
    if (serverRequestId) {
      rememberCaptureKey(path, serverRequestId);
      return serverRequestId;
    }

    const key = captureStorageKey(meId, wagon.id, path);
    const existing = captureKeys.current.get(key) || readStoredCaptureKey(key);
    if (isCanonicalUuid(existing)) {
      captureKeys.current.set(key, existing);
      return existing;
    }
    captureKeys.current.delete(key);
    removeStoredCaptureKey(key);
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
      if (path !== activeCapturePath) {
        const key = captureStorageKey(meId, wagon.id, path);
        captureKeys.current.delete(key);
        removeStoredCaptureKey(key);
      }
    }
    if (!activeCapturePath) {
      setCaptureUncertain(false);
      return;
    }

    const key = captureStorageKey(meId, wagon.id, activeCapturePath);
    if (serverCaptureRequestId && serverCaptureStatus === "processing") {
      captureKeys.current.set(key, serverCaptureRequestId);
      writeStoredCaptureKey(key, serverCaptureRequestId);
      setCaptureUncertain(true);
      return;
    }

    const stored = captureKeys.current.get(key) || readStoredCaptureKey(key);
    if (serverCaptureRequestId === stored && serverCaptureStatus && serverCaptureStatus !== "processing") {
      captureKeys.current.delete(key);
      removeStoredCaptureKey(key);
      setCaptureUncertain(false);
      return;
    }
    if (isCanonicalUuid(stored)) {
      captureKeys.current.set(key, stored);
      setCaptureUncertain(true);
      return;
    }
    captureKeys.current.delete(key);
    removeStoredCaptureKey(key);
    setCaptureUncertain(false);
  }, [activeCapturePath, meId, passage, serverCaptureRequestId, serverCaptureStatus, wagon.id]);

  async function act(path: PassageCapturePath) {
    const body = {};
    const capturePath =
      passage && (path === "entry-weight" || path === "exit-weight") ? (path as PassageCapturePath) : null;
    const idempotencyKey = capturePath ? getOrCreateCaptureKey(capturePath) : null;
    setBusy(true);
    onBusyChange(true);
    setError("");
    try {
      if (capturePath && idempotencyKey) {
        await api.post(`/grain/passages/${wagon.id}/${path}/`, body, {
          headers: { "Idempotency-Key": idempotencyKey },
        });
        clearCaptureKey(capturePath);
      } else {
        await api.post(`/grain/passages/${wagon.id}/${path}/`, body);
      }
      setCaptureUncertain(false);
      onChanged();
    } catch (e) {
      const response = (
        e as {
          response?: {
            status?: number;
            data?: {
              code?: unknown;
              request_id?: unknown;
              retryable?: unknown;
            };
          };
        }
      ).response;
      const responseRequestId = isCanonicalUuid(response?.data?.request_id) ? response.data.request_id : null;
      const responseCode = typeof response?.data?.code === "string" ? response.data.code : "";
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
      const retainCaptureKey = Boolean(
        capturePath &&
        idempotencyKey &&
        (retryable === true || (retryable !== false && (transientResponse || resumeResponse))),
      );
      const retainedRequestId = responseRequestId || idempotencyKey;
      if (capturePath && retainCaptureKey && retainedRequestId) {
        rememberCaptureKey(capturePath, retainedRequestId);
      } else if (capturePath) {
        clearCaptureKey(capturePath);
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
