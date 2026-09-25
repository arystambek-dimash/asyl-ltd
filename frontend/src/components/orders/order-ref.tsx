import Link from "next/link";
import { withBack } from "@/lib/navigation";
import { cn } from "@/lib/utils";

/**
 * Номер заказа в строке списка: ссылка на карточку, если её можно открыть, иначе просто текст.
 * `back` — куда вернёт кнопка «Назад» карточки; `linkClassName` — стиль только у ссылки.
 */
export function OrderRef({
  id,
  canOpen,
  back,
  className,
  linkClassName,
  children,
}: {
  id: number;
  canOpen: boolean;
  back?: string;
  className?: string;
  linkClassName?: string;
  children: React.ReactNode;
}) {
  if (!canOpen) return <span className={className}>{children}</span>;
  const href = back ? withBack(`/orders/${id}`, back) : `/orders/${id}`;
  return (
    <Link href={href} className={cn(className, "hover:underline", linkClassName)}>
      {children}
    </Link>
  );
}
