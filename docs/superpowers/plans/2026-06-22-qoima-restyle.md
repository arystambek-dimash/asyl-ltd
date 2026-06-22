# Qoima Restyle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle asyl-ltd CRM to qoima's look — warm Notion palette, blue accent, Inter font — by swapping CSS variable values and adding the font. No token renames, no component-class edits.

**Architecture:** Two files only. `globals.css` gets new `:root` (light) and `.dark` values mapped from qoima's palette onto asyl's existing token names. `layout.tsx` loads Inter via `next/font/google`. Every page/component already uses `var(--*)`, so they restyle automatically.

**Tech Stack:** Next.js 15 App Router, Tailwind v4, next/font.

## Global Constraints

- Keep asyl token NAMES (`--background`, `--foreground`, `--primary`, `--border`, `--ring`, `--sidebar-*`). Only change VALUES.
- Do NOT touch the `@theme inline` mapping block or any component class.
- primary-кнопки остаются ТЁМНЫМИ (ink); синий `#2383e2` (light) / `#4ea4ee` (dark) идёт в `--ring`/`--sidebar-ring`.
- `--radius` stays `0.625rem`. `--chart-1..5` unchanged.
- Inter with `subsets: ["latin", "cyrillic"]` (UI is Russian).
- No backend changes; `feat/weights` logic untouched.

---

### Task 1: Swap palette values in globals.css

**Files:**
- Modify: `frontend/src/app/globals.css` — `:root` block (lines 9-45) and `.dark` block (lines 47-75)

**Interfaces:**
- Produces: re-themed CSS variables. Consumed implicitly by every component via `var(--*)`.

- [ ] **Step 1: Replace the `:root` block**

Replace the entire `:root { ... }` block (lines 9-45) with:

```css
:root {
  --radius: 0.625rem;
  --background: #ffffff;
  --foreground: #37352f;
  --card: #ffffff;
  --card-foreground: #37352f;
  --popover: #ffffff;
  --popover-foreground: #37352f;
  --primary: #37352f;
  --primary-foreground: #ffffff;
  --secondary: #f7f6f3;
  --secondary-foreground: #37352f;
  --muted: #f7f6f3;
  --muted-foreground: #787671;
  --accent: #f7f6f3;
  --accent-foreground: #37352f;
  --destructive: #d8473a;
  --destructive-foreground: #ffffff;
  --success: #3d9c47;
  --warning: #d49a2a;
  --border: #ececea;
  --input: #e2e1de;
  --ring: #2383e2;
  --chart-1: oklch(0.646 0.222 41.116);
  --chart-2: oklch(0.6 0.118 184.704);
  --chart-3: oklch(0.398 0.07 227.392);
  --chart-4: oklch(0.828 0.189 84.429);
  --chart-5: oklch(0.769 0.188 70.08);
  --sidebar: #fbfbfa;
  --sidebar-foreground: #37352f;
  --sidebar-primary: #37352f;
  --sidebar-primary-foreground: #ffffff;
  --sidebar-accent: #efeeec;
  --sidebar-accent-foreground: #37352f;
  --sidebar-border: #ececea;
  --sidebar-ring: #2383e2;
}
```

- [ ] **Step 2: Replace the `.dark` block**

Replace the entire `.dark { ... }` block (lines 47-75) with:

```css
.dark {
  --background: #191919;
  --foreground: #e6e3dc;
  --card: #202020;
  --card-foreground: #e6e3dc;
  --popover: #252525;
  --popover-foreground: #e6e3dc;
  --primary: #e6e3dc;
  --primary-foreground: #191919;
  --secondary: #252525;
  --secondary-foreground: #e6e3dc;
  --muted: #252525;
  --muted-foreground: #9b9a94;
  --accent: #2f2f2f;
  --accent-foreground: #e6e3dc;
  --destructive: #d8473a;
  --destructive-foreground: #ffffff;
  --success: #3d9c47;
  --warning: #d49a2a;
  --border: #2a2a2a;
  --input: #373737;
  --ring: #4ea4ee;
  --sidebar: #202020;
  --sidebar-foreground: #e6e3dc;
  --sidebar-primary: #e6e3dc;
  --sidebar-primary-foreground: #191919;
  --sidebar-accent: #2f2f2f;
  --sidebar-accent-foreground: #e6e3dc;
  --sidebar-border: #2a2a2a;
  --sidebar-ring: #4ea4ee;
}
```

- [ ] **Step 3: Verify the `@theme inline` block is untouched**

Run: `grep -n "@theme inline" frontend/src/app/globals.css`
Expected: still present (line ~77). Confirm `--color-*: var(--*)` mappings unchanged.

- [ ] **Step 4: Build to verify CSS compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds (Tailwind v4 consumes the new variables; no errors).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/globals.css
git commit -m "style: swap palette to qoima warm tones + blue accent"
```

---

### Task 2: Load Inter font + base typography

**Files:**
- Modify: `frontend/src/app/layout.tsx`
- Modify: `frontend/src/app/globals.css` — `body` rule (lines 122-127)

**Interfaces:**
- Consumes: `--font-sans` CSS variable set by next/font.
- Produces: Inter applied app-wide.

- [ ] **Step 1: Add Inter to layout.tsx**

Replace `frontend/src/app/layout.tsx` with:

```tsx
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin", "cyrillic"],
  variable: "--font-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "АСЫЛ-LTD — Система учёта",
  description: "Внутренняя CRM мукомольного цеха Асыл-LTD",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className={inter.variable}>
      <body>{children}</body>
    </html>
  );
}
```

- [ ] **Step 2: Apply the font + base size in globals.css `body`**

Replace the `body { ... }` rule (lines 122-127) with:

```css
body {
  background: var(--background);
  color: var(--foreground);
  font-family: var(--font-sans), -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  font-size: 15px;
  line-height: 1.5;
  font-feature-settings: "rlig" 1, "calt" 1;
  -webkit-font-smoothing: antialiased;
}
```

- [ ] **Step 3: Build to verify**

Run: `cd frontend && npm run build`
Expected: build succeeds; Inter is fetched and bundled by next/font (no network errors at build because next/font self-hosts).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/layout.tsx frontend/src/app/globals.css
git commit -m "style: load Inter font + base typography"
```

---

### Task 3: Visual verification in Docker

**Files:** none (verification).

- [ ] **Step 1: Build and run the stack**

Run: `docker compose up --build -d` then wait ~5s.

- [ ] **Step 2: Smoke-check the frontend serves**

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/login`
Expected: 200 (or 307 redirect to a login route — not 5xx).

- [ ] **Step 3: Visual check (manual)**

Open http://localhost:3000 — log in, view dashboard / orders / shipping. Confirm:
- Warm off-white background, ink (`#37352f`) text, not pure black.
- Blue focus ring / outlines on inputs and focused elements.
- Primary buttons are dark (ink), not blue.
- Inter font rendering (rounded, even).
- Toggle dark theme (topbar) → warm dark (`#191919`), blue accent `#4ea4ee`, no gold.

- [ ] **Step 4: Tear down**

Run: `docker compose down`

- [ ] **Step 5: No commit needed (verification only).**

---

## Notes for the implementer

- Tailwind v4 reads the CSS variables at build; changing values needs a rebuild to reflect (already covered by `npm run build`).
- If any component looked correct before only because of the old gold `--primary` in dark mode, it now shows ink — that's intended (spec: blue accent everywhere, dark primary buttons).
- `next/font` self-hosts Inter (no runtime Google fetch), so the build works offline and in Docker.
- Do not edit `tailwind` config (there is none — v4) or component `ui/` files. Pure token + font change.
