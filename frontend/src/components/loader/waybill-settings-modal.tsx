"use client";
import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import type { WaybillSettings } from "@/lib/loader";
import { showSuccess } from "@/lib/toast";
import { useApi } from "@/lib/use-api";

const MAX_SIGNERS = 6;

/** Название точки и подписи внизу накладной — меняются здесь, а не в коде. */
export function WaybillSettingsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data, error: loadError, reload } = useApi<WaybillSettings>(open ? "/loader/waybill-settings/" : null);
  const [draft, setDraft] = useState<WaybillSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (data) setDraft({ point_name: data.point_name, signers: data.signers.map((signer) => ({ ...signer })) });
  }, [data]);

  async function save() {
    if (!draft) return;
    setBusy(true);
    setError("");
    try {
      const signers = draft.signers.filter((signer) => signer.role.trim() || signer.name.trim());
      await api.put("/loader/waybill-settings/", { ...draft, signers });
      showSuccess("Настройки накладной сохранены");
      onClose();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  const updateSigner = (index: number, key: "role" | "name", value: string) =>
    setDraft(
      (current) =>
        current && {
          ...current,
          signers: current.signers.map((signer, i) => (i === index ? { ...signer, [key]: value } : signer)),
        },
    );

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Отгрузка"
      title="Накладная"
      description="Шапка и подписи, которые печатаются на каждой накладной."
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            Отмена
          </Button>
          <Button onClick={save} disabled={busy || !draft?.point_name.trim()}>
            {busy ? "Сохранение…" : "Сохранить"}
          </Button>
        </>
      }
    >
      {loadError && <ErrorAlert message={loadError} onRetry={reload} />}
      {draft && (
        <div className="flex flex-col gap-4">
          <Field label="Точка в шапке" htmlFor="waybill-point">
            <Input
              id="waybill-point"
              value={draft.point_name}
              onChange={(event) => setDraft({ ...draft, point_name: event.target.value })}
            />
          </Field>
          <div className="flex flex-col gap-2">
            <div className="text-[12px] font-medium">Подписи</div>
            {draft.signers.map((signer, index) => (
              <div key={index} className="flex gap-2">
                <Input
                  aria-label={`Должность, подпись ${index + 1}`}
                  placeholder="Кладовщик"
                  value={signer.role}
                  onChange={(event) => updateSigner(index, "role", event.target.value)}
                  className="w-2/5"
                />
                <Input
                  aria-label={`Фамилия, подпись ${index + 1}`}
                  placeholder="Тажи А"
                  value={signer.name}
                  onChange={(event) => updateSigner(index, "name", event.target.value)}
                />
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Убрать подпись ${index + 1}`}
                  onClick={() => setDraft({ ...draft, signers: draft.signers.filter((_, i) => i !== index) })}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            ))}
            {draft.signers.length < MAX_SIGNERS && (
              <Button
                variant="outline"
                size="sm"
                className="self-start"
                onClick={() => setDraft({ ...draft, signers: [...draft.signers, { role: "", name: "" }] })}
              >
                <Plus className="size-4" /> Добавить подпись
              </Button>
            )}
            <p className="text-xs text-[var(--muted-foreground)]">Строка «Получатель» печатается всегда.</p>
          </div>
          {error && (
            <p
              role="alert"
              className="rounded-md bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]"
            >
              {error}
            </p>
          )}
        </div>
      )}
    </Modal>
  );
}
