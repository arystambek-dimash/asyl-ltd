"use client";
import { useMemo, useState } from "react";
import { CheckCircle2, Clock, ImageIcon, Paperclip, Pencil, Plus, RotateCcw, Trash2, X } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Modal } from "@/components/ui/modal";
import { Badge } from "@/components/ui/badge";
import { DataGate } from "@/components/ui/data-state";
import { ActionMenu } from "@/components/ui/action-menu";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { VoiceRecorder } from "@/components/voice-recorder";
import { api, apiError } from "@/lib/api";
import { showSuccess } from "@/lib/toast";
import { useApi } from "@/lib/use-api";
import { can } from "@/lib/can";
import { useAuth } from "@/store/auth";
import { cn, formatDateTime } from "@/lib/utils";
import type { Task, TaskAssignee } from "@/lib/types";

type Filter = "pending" | "done" | "all";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "pending", label: "В ожидании" },
  { key: "done", label: "Выполнено" },
  { key: "all", label: "Все" },
];

function AttachmentChip({ kind, url, name }: { kind: string; url: string | null; name: string }) {
  if (kind === "voice") {
    return (
      <audio src={url ?? undefined} controls aria-label={name || "Голосовое сообщение"} className="h-8 max-w-[220px]" />
    );
  }
  if (kind === "photo" && url) {
    return (
      <a href={url} target="_blank" rel="noreferrer" className="group relative">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={url}
          alt={name}
          className="size-16 rounded-lg border object-cover transition group-hover:opacity-80"
        />
      </a>
    );
  }
  return (
    <a
      href={url ?? "#"}
      target="_blank"
      rel="noreferrer"
      className="flex max-w-full items-center gap-1.5 break-all rounded-lg border px-2 py-1 text-xs hover:bg-[var(--muted)]"
    >
      <Paperclip className="size-3.5" /> {name || "файл"}
    </a>
  );
}

function TaskCard({
  task,
  onChanged,
  onEdit,
  onDelete,
}: {
  task: Task;
  onChanged: () => void;
  onEdit?: () => void;
  onDelete?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const done = task.status === "done";

  async function toggle() {
    setBusy(true);
    setError("");
    try {
      await api.post(`/tasks/${task.id}/${done ? "reopen" : "complete"}/`);
      onChanged();
      showSuccess(done ? "Задача снова в работе" : "Задача выполнена");
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  const photos = task.attachments.filter((a) => a.kind === "photo");
  const rest = task.attachments.filter((a) => a.kind !== "photo");

  return (
    <div
      className={cn(
        "rounded-[20px] border border-[var(--border)] bg-[var(--card)] p-4 shadow-card transition-[border-color,box-shadow,opacity] hover:border-[var(--ring)]/25 sm:p-5",
        done && "opacity-70",
      )}
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={cn(
                "min-w-0 break-words font-semibold",
                done && "line-through decoration-[var(--muted-foreground)]",
              )}
            >
              {task.title}
            </span>
            <Badge tone={done ? "success" : "warning"} dot>
              {task.status_label}
            </Badge>
            {task.due_date && !done && (
              <span className="flex items-center gap-1 text-xs text-[var(--muted-foreground)]">
                <Clock className="size-3.5" /> до {task.due_date.split("-").reverse().join(".")}
              </span>
            )}
          </div>
          {task.body && (
            <p className="mt-1.5 whitespace-pre-wrap break-words text-sm text-[var(--muted-foreground)]">{task.body}</p>
          )}

          {(photos.length > 0 || rest.length > 0) && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {photos.map((a) => (
                <AttachmentChip key={a.id} kind={a.kind} url={a.url} name={a.original_name} />
              ))}
              {rest.map((a) => (
                <AttachmentChip key={a.id} kind={a.kind} url={a.url} name={a.original_name} />
              ))}
            </div>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--muted-foreground)]">
            <span>
              Исполнитель: <b className="text-[var(--foreground)]">{task.assignee_name ?? "—"}</b>
            </span>
            {task.created_by_name && <span>Поставил: {task.created_by_name}</span>}
            <span>{formatDateTime(task.created_at)}</span>
            {done && task.done_at && (
              <span className="text-[var(--success)]">
                Выполнено {formatDateTime(task.done_at)}
                {task.done_by_name ? ` · ${task.done_by_name}` : ""}
              </span>
            )}
          </div>
          {error && (
            <p role="alert" className="mt-2 text-xs text-[var(--destructive)]">
              {error}
            </p>
          )}
        </div>

        <div className="flex w-full shrink-0 items-center gap-1.5 sm:w-auto">
          {task.can_complete && (
            <Button
              className="flex-1 sm:flex-none"
              size="sm"
              variant={done ? "ghost" : "default"}
              disabled={busy}
              onClick={() => void toggle()}
            >
              {done ? (
                <>
                  <RotateCcw className="size-4" /> Вернуть
                </>
              ) : (
                <>
                  <CheckCircle2 className="size-4" /> Выполнено
                </>
              )}
            </Button>
          )}
          {(onEdit || onDelete) && (
            <ActionMenu
              items={[
                ...(onEdit ? [{ key: "edit", label: "Изменить", icon: Pencil, onSelect: onEdit }] : []),
                ...(onDelete
                  ? [
                      {
                        key: "delete",
                        label: "Удалить",
                        icon: Trash2,
                        tone: "destructive" as const,
                        onSelect: onDelete,
                      },
                    ]
                  : []),
              ]}
            />
          )}
        </div>
      </div>
    </div>
  );
}

export default function TasksPage() {
  const { me } = useAuth();
  const canCreate = can(me, "tasks.create");
  const [filter, setFilter] = useState<Filter>("pending");
  const query = filter === "all" ? "" : `?status=${filter}`;
  const { data, loading, error, reload } = useApi<Task[]>(`/tasks/${query}`);

  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [assignee, setAssignee] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [photos, setPhotos] = useState<File[]>([]);
  const [voice, setVoice] = useState<File | null>(null);
  const [extrasOpen, setExtrasOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  // Задачу можно поправить или снять: опечатка в заголовке, не тот
  // исполнитель или продублированная постановка — раньше жили навсегда.
  const [editing, setEditing] = useState<Task | null>(null);
  const [deleting, setDeleting] = useState<Task | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  const { data: assignees } = useApi<TaskAssignee[]>(canCreate ? "/task-assignees/" : null);

  const counts = useMemo(() => {
    const rows = data ?? [];
    return { total: rows.length };
  }, [data]);

  function resetForm() {
    setTitle("");
    setBody("");
    setAssignee("");
    setDueDate("");
    setPhotos([]);
    setVoice(null);
    setExtrasOpen(false);
    setFormError("");
    setEditing(null);
  }

  function openEdit(task: Task) {
    resetForm();
    setEditing(task);
    setTitle(task.title);
    setBody(task.body);
    setAssignee(String(task.assignee));
    setDueDate(task.due_date ?? "");
    setExtrasOpen(Boolean(task.due_date));
    setOpen(true);
  }

  async function confirmDelete() {
    if (!deleting) return;
    setDeleteBusy(true);
    setDeleteError("");
    try {
      await api.delete(`/tasks/${deleting.id}/`);
      setDeleting(null);
      reload();
      showSuccess("Задача удалена");
    } catch (cause) {
      setDeleteError(apiError(cause));
    } finally {
      setDeleteBusy(false);
    }
  }

  async function submit() {
    setSaving(true);
    setFormError("");
    try {
      if (editing) {
        await api.patch(`/tasks/${editing.id}/`, {
          title,
          body,
          assignee: Number(assignee),
          due_date: dueDate || null,
        });
        setOpen(false);
        resetForm();
        reload();
        showSuccess("Задача обновлена");
        return;
      }
      const form = new FormData();
      form.append("title", title);
      form.append("body", body);
      form.append("assignee", assignee);
      if (dueDate) form.append("due_date", dueDate);
      photos.forEach((file) => form.append("attachments", file));
      if (voice) form.append("attachments", voice);
      await api.post("/tasks/", form, { headers: { "Content-Type": "multipart/form-data" } });
      setOpen(false);
      resetForm();
      reload();
      showSuccess("Задача поставлена");
    } catch (cause) {
      setFormError(apiError(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <AppShell
      title="Задачи"
      section="Работа"
      actions={
        canCreate ? (
          <Button
            size="sm"
            onClick={() => {
              resetForm();
              setOpen(true);
            }}
          >
            <Plus className="size-4" /> Поставить задачу
          </Button>
        ) : undefined
      }
    >
      <div
        className="mb-4 flex w-full rounded-xl border border-[var(--border)] bg-[var(--muted)]/70 p-1 sm:inline-flex sm:w-auto"
        role="group"
        aria-label="Фильтр задач"
      >
        {FILTERS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setFilter(item.key)}
            aria-pressed={filter === item.key}
            className={cn(
              "min-h-10 flex-1 rounded-lg px-3 py-2 text-sm font-semibold transition-[background-color,color,box-shadow] sm:flex-none sm:px-4",
              filter === item.key
                ? "bg-[var(--card)] shadow-sm"
                : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      {!data ? (
        <DataGate loading={loading} error={error} onRetry={reload} />
      ) : counts.total === 0 ? (
        <div className="flex min-h-56 flex-col items-center justify-center rounded-[20px] border border-dashed border-[var(--border)] bg-[var(--card)]/60 px-5 text-center text-[var(--muted-foreground)]">
          <CheckCircle2 className="mb-2 size-8 opacity-40" />
          <p className="font-semibold">{filter === "done" ? "Выполненных задач нет" : "Задач нет"}</p>
          {canCreate && filter !== "done" && <p className="mt-1 text-sm">Нажмите «Поставить задачу».</p>}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {data.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              onChanged={reload}
              onEdit={canCreate ? () => openEdit(task) : undefined}
              onDelete={me?.is_superuser || (me && task.created_by === me.id) ? () => setDeleting(task) : undefined}
            />
          ))}
        </div>
      )}

      <Modal
        open={open}
        onClose={() => !saving && setOpen(false)}
        title={editing ? `Изменить задачу #${editing.id}` : "Новая задача"}
        description={
          editing
            ? "Заголовок, детали, исполнитель и срок. Вложения меняются в самой задаче."
            : "Опишите текстом или запишите голосом, приложите фото и выберите исполнителя."
        }
        className="max-w-lg"
        footer={
          <>
            <Button variant="ghost" disabled={saving} onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button disabled={saving || !title.trim() || !assignee} onClick={() => void submit()}>
              {editing ? (
                <>
                  <Pencil className="size-4" /> {saving ? "Сохранение…" : "Сохранить"}
                </>
              ) : (
                <>
                  <Plus className="size-4" /> {saving ? "Постановка…" : "Поставить"}
                </>
              )}
            </Button>
          </>
        }
      >
        <div className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="task-title">Что нужно сделать</Label>
            <Input
              id="task-title"
              value={title}
              autoFocus
              maxLength={200}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Например: почистить бункер №2"
            />
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="task-body">Подробности</Label>
            <textarea
              id="task-body"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="Необязательно"
              className="min-h-24 w-full resize-y rounded-xl border border-[var(--input)] bg-[var(--card)] px-3.5 py-3 text-sm shadow-[0_2px_8px_-7px_rgba(23,32,27,.5)] outline-none transition-[border-color,box-shadow,background-color] placeholder:text-[var(--muted-foreground)]/65 focus-visible:border-[var(--ring)] focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/14 disabled:cursor-not-allowed disabled:bg-[var(--muted)] disabled:opacity-70"
            />
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="task-assignee">Исполнитель</Label>
            <Select id="task-assignee" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
              <option value="">Выберите сотрудника</option>
              {(assignees ?? []).map((person) => (
                <option key={person.id} value={person.id}>
                  {person.name}
                  {person.position ? ` · ${person.position}` : ""}
                </option>
              ))}
            </Select>
          </div>

          {/* Срок, голос и фото нужны не каждой задаче: в свёрнутом виде форма
              умещается без прокрутки, и обычная постановка — это два поля. */}
          {!extrasOpen ? (
            <button
              type="button"
              onClick={() => setExtrasOpen(true)}
              className="flex items-center justify-center gap-2 rounded-xl border border-dashed px-3 py-2.5 text-sm font-medium text-[var(--muted-foreground)] transition hover:border-[var(--primary)] hover:text-[var(--foreground)]"
            >
              <Plus className="size-4" /> {editing ? "Срок" : "Срок, голос, фото"}
            </button>
          ) : (
            <div className="grid gap-4 rounded-xl border bg-[var(--muted)]/40 p-3">
              <div className="grid gap-1.5">
                <Label htmlFor="task-due">Срок</Label>
                <Input id="task-due" type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
              </div>

              {!editing && (
                <div className="grid gap-1.5">
                  <Label>Голосовое сообщение</Label>
                  <VoiceRecorder onChange={setVoice} disabled={saving} />
                </div>
              )}

              {!editing && (
                <div className="grid gap-1.5">
                  <Label htmlFor="task-photos">Фото</Label>
                  <input
                    id="task-photos"
                    type="file"
                    accept="image/*"
                    multiple
                    aria-describedby="task-photos-hint"
                    onChange={(e) => setPhotos(Array.from(e.target.files ?? []))}
                    className="w-full min-w-0 cursor-pointer rounded-xl border border-dashed border-[var(--input)] bg-[var(--card)] px-2 py-2 text-sm text-[var(--muted-foreground)] outline-none transition-[border-color,box-shadow] file:mr-3 file:cursor-pointer file:rounded-lg file:border-0 file:bg-[var(--muted)] file:px-3 file:py-2 file:text-sm file:font-semibold file:text-[var(--foreground)] hover:border-[var(--ring)]/40 focus-visible:border-[var(--ring)] focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/14"
                  />
                  <p id="task-photos-hint" className="text-xs text-[var(--muted-foreground)]">
                    Можно выбрать несколько фотографий.
                  </p>
                  {photos.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {photos.map((file) => (
                        <span
                          key={file.name}
                          className="flex max-w-full items-center gap-1 break-all rounded-lg bg-[var(--card)] px-2 py-1 text-xs"
                        >
                          <ImageIcon className="size-3.5" /> {file.name}
                        </span>
                      ))}
                      <button
                        type="button"
                        onClick={() => setPhotos([])}
                        className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                      >
                        <X className="size-3.5" /> очистить
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {formError && (
            <p
              role="alert"
              className="rounded-xl border border-[var(--destructive)]/30 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]"
            >
              {formError}
            </p>
          )}
        </div>
      </Modal>

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => !deleteBusy && setDeleting(null)}
        title="Удалить задачу?"
        description={deleting ? `«${deleting.title}» исчезнет у исполнителя вместе с вложениями.` : ""}
        busy={deleteBusy}
        error={deleteError}
        onConfirm={() => void confirmDelete()}
      />
    </AppShell>
  );
}
