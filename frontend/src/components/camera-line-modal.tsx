"use client";
import { useEffect, useRef, useState } from "react";
import type { AxiosError } from "axios";
import { Check, LoaderCircle, ShieldCheck } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { CameraLineEditor } from "@/components/camera-line-editor";
import { Button } from "@/components/ui/button";
import { FormError } from "@/components/ui/data-state";
import { Modal } from "@/components/ui/modal";
import {
  countingLineSaveBody,
  defaultCountingLine,
  lineSetupError,
  normalizeVerificationLines,
  verificationLinesSupported,
  type LineDirection,
  type NormalizedLine,
  type VerificationLine,
} from "@/lib/camera-counting-line";
import type { CameraCountingLine } from "@/lib/types";
import { useVisiblePolling } from "@/lib/use-visible-polling";

const LINE_SYNC_MS = 3 * 1000;

interface CameraCountingLineSave extends CameraCountingLine {
  saved?: boolean;
  applied_to_processor?: boolean;
  detail?: string;
  code?: string;
}

const SAVED_NOT_APPLIED = "Сохранено, но не применено к камере — обновите статус";
const STILL_NOT_APPLIED = "Камера всё ещё работает со старыми линиями. Сохраните ещё раз.";

function countingLineSignature(config: CameraCountingLine | null | undefined) {
  const line = config?.line;
  const checks = normalizeVerificationLines(config?.verification_lines).map((item) =>
    [item.id, item.name, item.line.x1, item.line.y1, item.line.x2, item.line.y2].join(":"),
  );
  return [
    config?.updated_at ?? "",
    config?.direction ?? "any",
    // An AI-service upgrade changes only this: the editor must follow it.
    verificationLinesSupported(config),
    line?.x1 ?? "",
    line?.y1 ?? "",
    line?.x2 ?? "",
    line?.y2 ?? "",
    ...checks,
  ].join("|");
}

interface LineDraft {
  line: NormalizedLine;
  direction: LineDirection;
  verification: VerificationLine[];
  verificationSupported: boolean;
}

function draftFromConfig(config: CameraCountingLine | null | undefined): LineDraft {
  return {
    line: config?.line ? { ...config.line } : defaultCountingLine(),
    direction: config?.direction ?? "any",
    verification: normalizeVerificationLines(config?.verification_lines),
    verificationSupported: verificationLinesSupported(config),
  };
}

/**
 * Редактор линий подсчёта и проверки одной камеры. Монтируется на время
 * редактирования (с key по камере): черновик стартует с линии из инвентаря и
 * заменяется сохранённой на AI-сервисе, как только она загрузится.
 */
export function CameraLineModal({
  camera,
  onClose,
  onConfigChange,
}: {
  camera: { src: string; zone: string; line_config?: CameraCountingLine | null };
  onClose: () => void;
  /** Сохранённая конфигурация камеры изменилась: обновить инвентарь. */
  onConfigChange: (src: string, config: CameraCountingLine) => void;
}) {
  const { src } = camera;
  const lineUrl = `/cameras/${encodeURIComponent(src)}/counting-line`;
  const requestId = useRef(0);
  const dirty = useRef(false);
  const serverSignature = useRef(countingLineSignature(camera.line_config));
  const [draft, setDraft] = useState(() => draftFromConfig(camera.line_config));
  const [pendingRemote, setPendingRemote] = useState<CameraCountingLine | null>(null);
  const [authoritative, setAuthoritative] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  // Saved on the AI service, but not (verifiably) live on the camera yet.
  const [warning, setWarning] = useState("");
  const setupProblem = lineSetupError(draft.line, draft.verification);

  function acceptConfig(config: CameraCountingLine) {
    setDraft(draftFromConfig(config));
    setAuthoritative(true);
    dirty.current = false;
    serverSignature.current = countingLineSignature(config);
    setPendingRemote(null);
    onConfigChange(src, config);
  }

  /**
   * Read the authoritative saved lines; null when superseded or failed.
   * ``checkApplied`` also asks whether the running camera uses them.
   */
  async function loadConfig(checkApplied = false) {
    const id = ++requestId.current;
    setLoading(true);
    try {
      const response = await api.get<CameraCountingLine>(lineUrl, {
        timeout: 10_000,
        ...(checkApplied ? { params: { applied: 1 } } : {}),
      });
      if (requestId.current !== id) return null;
      acceptConfig(response.data);
      return response.data;
    } catch (cause) {
      if (requestId.current === id) setError(apiError(cause));
      return null;
    } finally {
      if (requestId.current === id) setLoading(false);
    }
  }

  // Закрытие окна отменяет ответы загрузки, начатой до него.
  useEffect(() => {
    const requests = requestId;
    void loadConfig();
    return () => {
      requests.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- компонент монтируется на одну камеру
  }, []);

  /** The saved file alone proves nothing: ask the running camera. */
  async function refreshStatus() {
    setError("");
    setNotice("");
    const config = await loadConfig(true);
    if (!config) return;
    if (config.line_applied === "applied" || config.line_applied === "not_running") {
      setWarning("");
      setNotice(
        config.line_applied === "applied"
          ? "Линии применены к камере."
          : "Модель на камере не запущена — линии применятся при следующем запуске.",
      );
    } else {
      setWarning(STILL_NOT_APPLIED);
    }
  }

  function editDraft(change: Partial<LineDraft>) {
    dirty.current = true;
    setDraft((current) => ({ ...current, ...change }));
    setNotice("");
    setError("");
    setWarning("");
  }

  // An editor can stay open while another administrator calibrates the same
  // camera. Poll the exact lightweight endpoint: clean drafts follow remote
  // changes automatically, while dirty drafts surface a conflict instead of
  // being silently overwritten.
  useVisiblePolling(async ({ signal }) => {
    // Only observe the counter: a newer load, save or close drops this
    // reply, but a best-effort poll must never supersede the load that
    // holds the editor in «Загружаем…».
    const id = requestId.current;
    try {
      const response = await api.get<CameraCountingLine>(lineUrl, {
        timeout: 10_000,
        signal,
      });
      if (signal.aborted || requestId.current !== id) return;
      const remote = response.data;
      const remoteSignature = countingLineSignature(remote);
      if (remoteSignature === serverSignature.current) return;
      if (dirty.current) {
        onConfigChange(src, remote);
        setPendingRemote((current) => (countingLineSignature(current) === remoteSignature ? current : remote));
      } else {
        acceptConfig(remote);
        setError("");
        setNotice("Линия обновлена из настроек камеры.");
      }
    } catch {
      // The initial load already exposes connectivity errors. Background
      // sync is best-effort and retries without replacing a usable draft.
    }
  }, LINE_SYNC_MS);

  function savedConfig(payload?: Partial<CameraCountingLine>): CameraCountingLine {
    return {
      configured: payload?.configured ?? true,
      coordinate_space: "normalized",
      line: payload?.line ? { ...payload.line } : { ...draft.line },
      line_spec: payload?.line_spec ?? null,
      direction: payload?.direction ?? draft.direction,
      updated_at: payload?.updated_at ?? new Date().toISOString(),
      verification_lines: payload?.verification_lines ?? (draft.verificationSupported ? draft.verification : []),
      verification_lines_supported: payload?.verification_lines_supported ?? draft.verificationSupported,
    };
  }

  async function save() {
    if (!authoritative || pendingRemote || setupProblem) return;
    requestId.current += 1; // an older sync GET may not roll this save back
    const sentVerification = draft.verificationSupported ? draft.verification : null;
    const several = !!sentVerification?.length;
    setSaving(true);
    setError("");
    setNotice("");
    setWarning("");
    try {
      const response = await api.put<CameraCountingLineSave>(
        lineUrl,
        countingLineSaveBody(draft.line, draft.direction, sentVerification),
        { timeout: 12_000 },
      );
      acceptConfig(savedConfig(response.data));
      if (several && response.data.verification_lines_supported === false) {
        setWarning("Основная линия сохранена, но AI-сервис не сохранил линии проверки. Обновите AI-сервис.");
      } else {
        const pending = response.data.applied_to_processor === false;
        setNotice(
          several
            ? pending
              ? "Линии сохранены. Они применятся при следующем запуске модели."
              : "Линии сохранены и готовы к подсчёту."
            : pending
              ? "Линия сохранена. Она применится при следующем запуске модели."
              : "Линия сохранена и готова к подсчёту.",
        );
      }
    } catch (cause) {
      const payload = (cause as AxiosError<CameraCountingLineSave>).response?.data;
      if (payload?.saved) {
        // The PUT reply is the saved state; the processor has not confirmed it.
        acceptConfig(savedConfig(payload));
        setWarning(SAVED_NOT_APPLIED);
      } else {
        setError(apiError(cause));
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      eyebrow="Только для суперпользователя"
      title="Линии подсчёта и проверки"
      description={`${camera.zone} · проведите линии на живом видео или на снимке кадра.`}
      className="max-w-4xl"
      footer={
        <>
          <div className="mr-auto hidden items-center gap-2 text-xs text-[var(--muted-foreground)] sm:flex">
            <ShieldCheck className="size-4 text-emerald-600" />
            Настройка защищена правами superuser
          </div>
          <Button variant="ghost" onClick={onClose}>
            Закрыть
          </Button>
          <Button
            disabled={loading || saving || !authoritative || !!pendingRemote || !!setupProblem}
            onClick={() => void save()}
            className="min-w-36 bg-sky-600 text-white hover:bg-sky-700"
          >
            {saving ? (
              <>
                <LoaderCircle className="size-4 animate-spin" /> Сохранение…
              </>
            ) : (
              <>
                <Check className="size-4" /> Сохранить линии
              </>
            )}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <CameraLineEditor
          src={src}
          line={draft.line}
          direction={draft.direction}
          disabled={loading || saving || !authoritative}
          verificationLines={draft.verification}
          verificationSupported={draft.verificationSupported}
          onLineChange={(line) => editDraft({ line })}
          onDirectionChange={(direction) => editDraft({ direction })}
          onVerificationLinesChange={(verification) => editDraft({ verification })}
        />
        {loading && (
          <div className="flex items-center gap-2 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-800">
            <LoaderCircle className="size-4 animate-spin" /> Загружаем сохранённую линию…
          </div>
        )}
        {setupProblem && !loading && (
          <p className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            {setupProblem}
          </p>
        )}
        {pendingRemote && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <p className="font-medium">Линию изменил другой пользователь.</p>
            <p className="mt-1 text-xs text-amber-800/80">
              Ваш черновик сохранён на экране. Выберите, какую версию продолжить редактировать.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => {
                  acceptConfig(pendingRemote);
                  setNotice("Загружена новая настройка камеры.");
                }}
              >
                Загрузить новую
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => {
                  serverSignature.current = countingLineSignature(pendingRemote);
                  setPendingRemote(null);
                  setNotice("Оставлен ваш черновик. Сохранение заменит новую настройку.");
                }}
              >
                Оставить мой вариант
              </Button>
            </div>
          </div>
        )}
        {warning && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <span className="font-medium">{warning}</span>
            <Button type="button" size="sm" variant="outline" disabled={loading} onClick={() => void refreshStatus()}>
              Обновить статус
            </Button>
          </div>
        )}
        {notice && (
          <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-800">
            {notice}
          </p>
        )}
        <FormError message={error} className="rounded-lg px-4 py-3" />
      </div>
    </Modal>
  );
}
