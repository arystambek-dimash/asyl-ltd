import type { ComponentProps } from "react";

/**
 * `next/link` для vitest — обычная ссылка без предзагрузки. Подключать так:
 * `vi.mock("next/link", () => import("@/test-utils/next-link"))`.
 */
export default function Link({ children, ...props }: ComponentProps<"a">) {
  return <a {...props}>{children}</a>;
}
