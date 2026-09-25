import type { ComponentProps } from "react";

/**
 * `next/image` для vitest — обычный `<img>` без оптимизации. Подключать так:
 * `vi.mock("next/image", () => import("@/test-utils/next-image"))`.
 */
export default function Image({
  alt,
  priority,
  unoptimized,
  ...props
}: ComponentProps<"img"> & { priority?: boolean; unoptimized?: boolean }) {
  void priority;
  void unoptimized;
  // eslint-disable-next-line @next/next/no-img-element
  return <img alt={alt ?? ""} {...props} />;
}
