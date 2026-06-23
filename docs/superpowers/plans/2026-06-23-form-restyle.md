# Form Restyle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make modal forms look like qoima — header with eyebrow + big title + description + divider, sectioned fields with normal-case labels, and a footer with a divider and right-aligned buttons.

**Architecture:** Extend `Modal` with optional `eyebrow`/`description`/`footer` props (backward compatible), add a `Field` wrapper (Label + child + hint), normalize `Label` to qoima style, then convert all 8 modal forms to use the new header/footer/Field with sections. Submit buttons move to the footer and link to their form via `form={id}`.

**Tech Stack:** Next.js 15, React, Tailwind v4, lucide-react.

## Global Constraints

- All Modal additions are OPTIONAL — existing `<Modal title>` usages keep working.
- Form logic (state, submit handlers, validation, permission gates) does NOT change — only markup.
- Submit button lives in the Modal `footer`; the `<form>` gets an `id` and the submit `<Button type="submit" form={id}>`.
- `Field` props: `{ label?, hint?, children, className? }`. `Label` becomes normal-case `text-[12px] font-medium`.
- Section: `<section className="space-y-3 pt-4 border-t border-[var(--border)]">` (first section omits `pt-4 border-t`) with `<h4 className="text-[12px] font-medium text-[var(--muted-foreground)]">`.
- Verify each task with `cd frontend && npm run build`. No component unit tests.

---

### Task 1: Modal (eyebrow/description/footer) + Field + Label

**Files:**
- Modify: `frontend/src/components/ui/modal.tsx`
- Create: `frontend/src/components/ui/field.tsx`
- Modify: `frontend/src/components/ui/label.tsx`

**Interfaces:**
- Produces:
  - `Modal({ open, onClose, title, eyebrow?, description?, footer?, children, className })`.
  - `Field({ label?, hint?, children, className? })`.
  - `Label` normal-case style.

- [ ] **Step 1: Rewrite `frontend/src/components/ui/modal.tsx`**

```tsx
"use client";
import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export function Modal({
  open,
  onClose,
  title,
  eyebrow,
  description,
  footer,
  children,
  className,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  eyebrow?: string;
  description?: string;
  footer?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  const [mounted, setMounted] = useState(false);
  const titleId = useId();

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    const previousOverflow = document.body.style.overflow;
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open || !mounted) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
    >
      <div
        className="absolute inset-0 bg-black/55 backdrop-blur-[1px] animate-modal-backdrop"
        onClick={onClose}
      />
      <div
        className={cn(
          "relative z-10 flex max-h-[calc(100dvh-2rem)] w-full max-w-lg flex-col overflow-hidden rounded-xl border bg-[var(--card)] shadow-2xl animate-modal-content",
          className
        )}
      >
        <div className="relative border-b px-6 pb-4 pt-6">
          {eyebrow && (
            <div className="text-[12px] text-[var(--muted-foreground)]">{eyebrow}</div>
          )}
          <h2 id={titleId} className="text-[22px] font-bold tracking-tight">{title}</h2>
          {description && (
            <p className="mt-1 text-[14px] text-[var(--muted-foreground)]">{description}</p>
          )}
          <button
            type="button"
            onClick={onClose}
            className="absolute right-4 top-4 inline-flex size-8 shrink-0 items-center justify-center rounded-md text-[var(--muted-foreground)] transition-colors hover:bg-[var(--accent)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/50"
            aria-label="Закрыть"
          >
            <X className="size-4" />
          </button>
        </div>
        <div className="overflow-y-auto p-6">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 border-t bg-[var(--muted)]/40 px-6 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
```

- [ ] **Step 2: Create `frontend/src/components/ui/field.tsx`**

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";
import { Label } from "./label";

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label?: React.ReactNode;
  hint?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col", className)}>
      {label && <Label>{label}</Label>}
      {children}
      {hint && <p className="mt-1.5 text-[12px] text-[var(--muted-foreground)]">{hint}</p>}
    </div>
  );
}
```

- [ ] **Step 3: Normalize `frontend/src/components/ui/label.tsx`**

Replace the className in the Label:

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

export const Label = React.forwardRef<
  HTMLLabelElement,
  React.LabelHTMLAttributes<HTMLLabelElement>
>(({ className, ...props }, ref) => (
  <label
    ref={ref}
    className={cn(
      "mb-1.5 block text-[12px] font-medium text-[var(--foreground)]",
      className
    )}
    {...props}
  />
));
Label.displayName = "Label";
```

- [ ] **Step 4: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds (no form passes new props yet — backward compatible).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/modal.tsx frontend/src/components/ui/field.tsx frontend/src/components/ui/label.tsx
git commit -m "feat: Modal eyebrow/description/footer + Field + normal-case Label"
```

---

### Task 2: Employees form (the reference)

**Files:**
- Modify: `frontend/src/app/management/employees/page.tsx`

**Interfaces:**
- Consumes: `Modal` footer/eyebrow/description, `Field`.

The current form is a 2-col grid with Имя/Фамилия/Логин/Пароль/Телефон/Должность + full-width Роль + inline buttons. Convert to sectioned form with footer.

- [ ] **Step 1: Add the Field import**

In `frontend/src/app/management/employees/page.tsx` add:

```tsx
import { Field } from "@/components/ui/field";
```

- [ ] **Step 2: Replace the `<Modal>` block**

Replace the entire `<Modal open={open} ...>...</Modal>` with:

```tsx
      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow="Команда · Сотрудник"
        title="Новый сотрудник"
        description="Создайте аккаунт коллеге и выдайте доступ."
        className="max-w-xl"
        footer={
          <>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>Отмена</Button>
            <Button type="submit" form="employee-form" disabled={busy}>{busy ? "Сохранение…" : "Создать"}</Button>
          </>
        }>
        <form id="employee-form" onSubmit={submit} className="flex flex-col gap-5">
          <section className="space-y-3">
            <h4 className="text-[12px] font-medium text-[var(--muted-foreground)]">Человек</h4>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Имя">
                <Input value={form.first_name} required onChange={(e) => setForm({ ...form, first_name: e.target.value })} />
              </Field>
              <Field label="Фамилия">
                <Input value={form.last_name} required onChange={(e) => setForm({ ...form, last_name: e.target.value })} />
              </Field>
              <Field label="Логин">
                <Input value={form.username} required onChange={(e) => setForm({ ...form, username: e.target.value })} />
              </Field>
              <Field label="Пароль">
                <Input type="password" value={form.password} required minLength={6}
                  onChange={(e) => setForm({ ...form, password: e.target.value })} />
              </Field>
            </div>
          </section>

          <section className="space-y-3 border-t border-[var(--border)] pt-4">
            <h4 className="text-[12px] font-medium text-[var(--muted-foreground)]">Должность</h4>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Телефон">
                <Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
              </Field>
              <Field label="Должность">
                <Input value={form.position} onChange={(e) => setForm({ ...form, position: e.target.value })} />
              </Field>
            </div>
          </section>

          <section className="space-y-3 border-t border-[var(--border)] pt-4">
            <h4 className="text-[12px] font-medium text-[var(--muted-foreground)]">Роль</h4>
            <Field label="Роль">
              <Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                <option value="">Без роли</option>
                {(roles ?? []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </Select>
            </Field>
          </section>

          {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
        </form>
      </Modal>
```

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds. (The submit button in the footer targets the form via `form="employee-form"`.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/management/employees/page.tsx
git commit -m "feat: employees form qoima style (sections + footer)"
```

---

### Task 3: Clients + Products forms

**Files:**
- Modify: `frontend/src/app/clients/page.tsx`
- Modify: `frontend/src/app/catalog/products/page.tsx`

- [ ] **Step 1: Clients — read the ClientForm + its Modal**

Open `frontend/src/app/clients/page.tsx`. The `ClientForm` component renders fields and is wrapped by `<Modal title="Новый клиент">` in `ClientsPage`. Add `import { Field } from "@/components/ui/field";`. Update the `<Modal>` to pass `eyebrow="Работа · Клиент"`, keep `title="Новый клиент"`, add `description="Контакты и платёжные реквизиты клиента."`. Since `ClientForm` owns its own buttons, move them to the Modal `footer`: give the form `id="client-form"`, set the submit button `form="client-form"`, and pass the Отмена/submit buttons via `footer`. Wrap each labeled field in `<Field label="…">`. Group fields into a «Контакты» section and a «Реквизиты» section (the реквизиты fields already exist — wrap them in `<section className="space-y-3 border-t border-[var(--border)] pt-4">` with an `<h4>`). Remove the in-form footer `<div>` (now in Modal footer).

Because `ClientForm` is a separate component receiving `onCancel`/`onDone`, the simplest approach: keep `ClientForm` rendering the `<form id="client-form">` and fields, and have it ALSO expose its submit through `form="client-form"`. Pass the footer buttons from `ClientsPage` referencing `onCancel` and a submit button with `form="client-form"`. If `ClientForm`'s submit needs internal state (busy), instead keep buttons inside `ClientForm` but render them through a `footerSlot` — to avoid prop threading, KEEP the buttons inside ClientForm's `<form>` at the bottom but styled as before, and DON'T use Modal footer for clients (acceptable: clients keeps inline footer, gets eyebrow/description + Field/sections). This avoids cross-component prop threading.

Concretely for clients: pass `eyebrow`/`description` to Modal; inside `ClientForm`, wrap fields in `Field`, add section `<h4>`s, and keep the existing bottom button `<div className="flex justify-end gap-2 border-t pt-5 ...">`. No Modal `footer` prop for clients.

- [ ] **Step 2: Products — convert to Field + footer**

Open `frontend/src/app/catalog/products/page.tsx`. Add `import { Field } from "@/components/ui/field";`. The form is self-contained in `ProductsPage`. Replace the `<Modal>` block:

```tsx
      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow="Номенклатура · Товар"
        title="Новый товар"
        description="Сорт, цвет (тип) и фасовка."
        footer={
          <>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>Отмена</Button>
            <Button type="submit" form="product-form" disabled={busy}>{busy ? "Создание…" : "Создать"}</Button>
          </>
        }>
        <form id="product-form" onSubmit={add} className="flex flex-col gap-4">
          <Field label="Название">
            <Input value={name} autoFocus placeholder="напр. Высший сорт"
              onChange={(e) => setName(e.target.value)} required />
          </Field>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Цвет (тип)">
              <Select value={color} onChange={(e) => setColor(e.target.value)}>
                <option value="Red">Красный</option>
                <option value="Green">Зелёный</option>
                <option value="Blue">Синий</option>
              </Select>
            </Field>
            <Field label="Фасовка">
              <Select value={weight} onChange={(e) => setWeight(e.target.value)}>
                <option value="50">50 кг</option>
                <option value="25">25 кг</option>
              </Select>
            </Field>
          </div>
          <Field label="Цена за мешок, ₸">
            <Input type="number" step="0.01" value={price}
              onChange={(e) => setPrice(e.target.value)} required />
          </Field>
          {error && (
            <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
              {error}
            </p>
          )}
        </form>
      </Modal>
```

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/clients/page.tsx frontend/src/app/catalog/products/page.tsx
git commit -m "feat: clients + products forms qoima style"
```

---

### Task 4: Orders + Warehouse forms

**Files:**
- Modify: `frontend/src/app/orders/page.tsx`
- Modify: `frontend/src/app/warehouse/page.tsx`

- [ ] **Step 1: Orders — eyebrow/description + Field + footer**

Open `frontend/src/app/orders/page.tsx`. `NewOrderForm` is a separate component (like ClientForm). Add `import { Field } from "@/components/ui/field";`. On the `<Modal>` wrapping `NewOrderForm` add `eyebrow="Работа · Заказ"` and `description="Клиент, позиции и плановая дата прибытия."` (keep `title="Новый заказ"`). Inside `NewOrderForm`, wrap labeled fields in `<Field>`, keep a «Позиции» section heading (it likely already has one), and KEEP the existing inline footer buttons (avoid cross-component threading, same rationale as clients). Just upgrade labels/sections + header.

- [ ] **Step 2: Warehouse — Field + footer (two action buttons)**

Open `frontend/src/app/warehouse/page.tsx`. The adjust modal is inline in `WarehousePage`. Add `import { Field } from "@/components/ui/field";`. Replace the adjust `<Modal>` block:

```tsx
      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow="Склад · Корректировка"
        title="Изменить остаток"
        description="Добавьте приёмку или спишите мешки по товару."
        footer={
          <>
            <Button type="button" variant="outline" disabled={busy || !product || !amount}
              onClick={() => adjust(-1)}><Minus className="size-4" /> Списать</Button>
            <Button type="button" disabled={busy || !product || !amount}
              onClick={() => adjust(1)}><Plus className="size-4" /> Добавить</Button>
          </>
        }>
        <div className="flex flex-col gap-4">
          <Field label="Товар">
            <Select value={product} onChange={(e) => setProduct(e.target.value)}>
              <option value="">Выберите товар</option>
              {(products ?? []).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </Select>
          </Field>
          <Field label="Мешков">
            <Input type="number" min="1" value={amount}
              onChange={(e) => setAmount(e.target.value)} placeholder="0" />
          </Field>
          {error && (
            <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
              {error}
            </p>
          )}
        </div>
      </Modal>
```

(Warehouse uses buttons not a form-submit, so no `form` id needed — onClick handlers work directly in the footer.)

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/orders/page.tsx frontend/src/app/warehouse/page.tsx
git commit -m "feat: orders + warehouse forms qoima style"
```

---

### Task 5: Roles + Cameras forms

**Files:**
- Modify: `frontend/src/app/management/roles/page.tsx`
- Modify: `frontend/src/app/management/cameras/page.tsx`

- [ ] **Step 1: Roles — eyebrow/description + Field**

Open `frontend/src/app/management/roles/page.tsx`. The role modal title is `editing ? "Роль: {name}" : "Новая роль"`. Add `import { Field } from "@/components/ui/field";`. On the `<Modal>` add `eyebrow="Доступы · Роль"` and `description="Название и набор прав по разделам."`. Wrap the «Название роли» input in `<Field label="Название роли">`. The permission section already has its own structure — leave the permission buttons as-is (spec: don't restyle the permission matrix), just keep them under an `<h4 className="text-[12px] font-medium text-[var(--muted-foreground)]">Права</h4>` section heading. KEEP the existing inline footer buttons (the modal has dynamic title for edit; inline footer avoids threading).

- [ ] **Step 2: Cameras — both forms (create + bind)**

Open `frontend/src/app/management/cameras/page.tsx`. Add `import { Field } from "@/components/ui/field";`. There are two modals: «Новая камера» (create) and «Привязать камеру {id}» (bind). For each: add `eyebrow` (`"Управление · Камера"` for create, `"Управление · Привязка"` for bind) and a short `description`. Wrap each labeled input/select/textarea in `<Field label="…">`. KEEP the existing inline footer buttons in each.

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/management/roles/page.tsx frontend/src/app/management/cameras/page.tsx
git commit -m "feat: roles + cameras forms qoima style"
```

---

### Task 6: Visual verification in Docker

**Files:** none.

- [ ] **Step 1: Build and run**

Run: `docker compose up --build -d` then wait ~6s.

- [ ] **Step 2: Smoke-check serves**

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/login`
Expected: 200.

- [ ] **Step 3: Visual check (manual)**

Log in. Open each create/edit form (Employees, Clients, Products, Orders, Warehouse adjust, Roles, Cameras create + bind). Confirm: eyebrow line above a big bold title, description below, divider under header, normal-case field labels, section headings where present, and (for Employees/Products/Warehouse) a footer with a top divider + tinted background and right-aligned buttons. Submit each test form to confirm `form={id}` wiring works. Toggle dark theme.

- [ ] **Step 4: Tear down**

Run: `docker compose down`

- [ ] **Step 5: No commit (verification only).**

---

## Notes for the implementer

- The `form={id}` pattern lets a submit button outside the `<form>` (in Modal footer) still submit it. Only the forms that move buttons to the Modal footer (Employees, Products) need the `id`. Clients/Orders/Roles/Cameras keep inline footers to avoid cross-component prop threading — they still gain eyebrow/description + Field/sections.
- Warehouse uses onClick buttons (not submit), so its footer buttons work directly without a form id.
- `Label` change is global — every existing `<Label>` across the app becomes normal-case. That's intended (qoima look); scan for any place that relied on uppercase and accept the new look.
- Don't change any handler, state, validation, or permission gate — markup only.
- No tests; `npm run build` per task, Docker visual is the final gate.
