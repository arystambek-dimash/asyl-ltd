"use client";
import Image from "next/image";
import { useMemo, useState } from "react";
import { CheckCircle2, Clock, ImageIcon, Paperclip, Pencil, Plus, RotateCcw, Trash2, X } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Badge } from "@/components/ui/badge";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
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
  if (!url) {
    return (
      <span
        className="flex items-center gap-1.5 rounded-lg border px-2 py-1 text-xs text-[var(--muted-foreground)]"
        title="Файл недоступен"
      >
        <Paperclip className="size-3.5" />
        {name || "файл"}
        <span aria-hidden="true">·</span>
        <span>недоступен</span>
      </span>
    );
  }
  if (kind === "voice") {
    return <audio src={url} controls className="h-8 max-w-[220px]" />;
  }
  if (kind === "photo") {
    return (
      <a href={url} target="_blank" rel="noreferrer" className="group relative">
        <Image
          src={url}
          alt={name}
          width={64}
          height={64}
          unoptimized
          className="size-16 rounded-lg border object-cover transition group-hover:opacity-80"
        />
      </a>
    );
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="flex items-center gap-1.5 rounded-lg border px-2 py-1 text-xs hover:bg-[var(--muted)]"
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
    <div className={cn("rounded-xl border bg-[var(--card)] p-4 shadow-sm transition", done && "opacity-70")}>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn("font-semibold", done && "line-through decoration-[var(--muted-foreground)]")}>
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
            <p className="mt-1.5 whitespace-pre-wrap text-sm text-[var(--muted-foreground)]">{task.body}</p>
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
          {error && <p className="mt-2 text-xs text-[var(--destructive)]">{error}</p>}
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          {task.can_complete && (
            <Button size="sm" variant={done ? "ghost" : "default"} disabled={busy} onClick={() => void toggle()}>
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

  const {
    data: assignees,
    loading: assigneesLoading,
    error: assigneesError,
    reload: reloadAssignees,
  } = useApi<TaskAssignee[]>(canCreate ? "/task-assignees/" : null);

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
      <div className="mb-4 flex w-full rounded-xl border bg-[var(--muted)] p-1 sm:w-auto sm:inline-flex">
        {FILTERS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setFilter(item.key)}
            className={cn(
              "flex-1 rounded-lg px-4 py-2 text-sm font-semibold transition sm:flex-none",
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
        <div className="flex min-h-56 flex-col items-center justify-center rounded-xl border border-dashed text-center text-[var(--muted-foreground)]">
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
            <Button
              disabled={saving || assigneesLoading || !!assigneesError || !title.trim() || !assignee}
              onClick={() => void submit()}
            >
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
              className="min-h-20 w-full resize-y rounded-xl border bg-[var(--background)] px-3 py-2 text-sm outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)]/15"
            />
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="task-assignee">Исполнитель</Label>
            {assigneesError ? (
              <ErrorAlert message={assigneesError} onRetry={() => void reloadAssignees()} />
            ) : (
              <select
                id="task-assignee"
                value={assignee}
                disabled={assigneesLoading}
                onChange={(e) => setAssignee(e.target.value)}
                className="h-10 w-full rounded-xl border bg-[var(--background)] px-3 text-sm outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)]/15 disabled:cursor-wait disabled:opacity-60"
              >
                <option value="">{assigneesLoading ? "Загрузка сотрудников…" : "Выберите сотрудника"}</option>
                {(assignees ?? []).map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.name}
                    {person.position ? ` · ${person.position}` : ""}
                  </option>
                ))}
              </select>
            )}
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
                    onChange={(e) => setPhotos(Array.from(e.target.files ?? []))}
                    className="text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-[var(--card)] file:px-3 file:py-1.5 file:text-sm file:font-medium"
                  />
                  {photos.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {photos.map((file) => (
                        <span
                          key={file.name}
                          className="flex items-center gap-1 rounded-lg bg-[var(--card)] px-2 py-1 text-xs"
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
            <p className="rounded-xl border border-[var(--destructive)]/30 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
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
