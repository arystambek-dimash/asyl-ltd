import type { ReactNode } from "react";

/**
 * Пропускает содержимое страницы без проверки прав. Подключать так:
 * `vi.mock("@/components/require-perm", () => import("@/test-utils/require-perm"))`.
 */
export function RequirePerm({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
