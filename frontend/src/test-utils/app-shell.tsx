import type { ComponentProps } from "react";
import type { AppShell as RealAppShell } from "@/components/layout/app-shell";

/**
 * Заглушка оболочки страницы для vitest: без меню и шапки, только слоты —
 * раздел, заголовок, описание, «Назад», иконки справа, действия, вкладки и
 * контент в `<main>`, нижняя панель после него, как в настоящей оболочке.
 * Подключать так:
 * `vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"))`.
 */
export function AppShell({
  title,
  section,
  description,
  back,
  trailing,
  actions,
  tabs,
  children,
  footer,
}: ComponentProps<typeof RealAppShell>) {
  return (
    <>
      <main>
        {section && <p data-testid="section">{section}</p>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
        {back && (
          <button type="button" onClick={back.onClick}>
            {back.label}
          </button>
        )}
        {trailing}
        {actions}
        {tabs}
        {children}
      </main>
      <footer data-testid="footer">{footer}</footer>
    </>
  );
}
