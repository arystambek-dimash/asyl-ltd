"use client";
import { useCallback, useEffect, useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { can, deptLabel } from "@/lib/can";
import { cn } from "@/lib/utils";
import type { Me } from "@/lib/types";

const TOUR_DONE_KEY = "asyl_tour_v1";
export const TOUR_START_EVENT = "asyl:start-tour";

interface TourStep {
  /** значение data-tour подсвечиваемого элемента; без него — карточка по центру */
  target?: string;
  title: string;
  text: string;
}

// Шаги собираются под права пользователя — каждый видит только свои разделы.
function buildSteps(me: Me): TourStep[] {
  const field = deptLabel(me, "field");
  const steps: TourStep[] = [
    {
      target: "nav",
      title: "Меню навигации",
      text: "Слева — разделы системы. На телефоне меню открывается кнопкой ☰ в левом верхнем углу.",
    },
  ];
  if (can(me, "orders.view")) steps.push({
    target: "nav:/orders",
    title: "Заказы",
    text: "Все заказы клиентов: создание, редактирование до начала загрузки, статусы и оплата. Карандаш в строке — быстрое изменение.",
  });
  if (can(me, "dept2.view")) steps.push({
    target: "nav:/city/orders",
    title: `Заявки ${field}`,
    text: "Заявки выездного отдела: собирайте с выезда, запрашивайте и принимайте оплату прямо у клиента.",
  });
  if (can(me, "payments.confirm")) steps.push({
    target: "nav:/accounting",
    title: "Табло бухгалтера",
    text: "Подтверждение заявок и сверка оплат по обоим отделам. Сверенные оплаты уходят в кассу.",
  });
  if (can(me, "payments.cashier")) steps.push({
    target: "nav:/cashier",
    title: "Касса",
    text: "Финальное подтверждение поступления денег. Пока кассир не подтвердил — оплата не считается полученной.",
  });
  if (can(me, "warehouse.view")) steps.push({
    target: "nav:/warehouse",
    title: "Склад",
    text: "Остатки готовой продукции. Кнопка «Изменить остаток» — приёмка и списание с предпросмотром «сейчас → станет».",
  });
  if (can(me, "reports.view")) steps.push({
    target: "nav:/debts",
    title: "Долги",
    text: "Кто и сколько должен: по клиентам и магазинам, с окнами оплат по расписанию.",
  });
  steps.push({
    target: "profile",
    title: "Профиль",
    text: "Здесь смена темы и выход. Обучение можно пройти снова — кнопка «?» рядом с темой.",
  });
  return steps;
}

interface Rect { top: number; left: number; width: number; height: number; }

function targetRect(target?: string): Rect | null {
  if (!target) return null;
  const el = document.querySelector<HTMLElement>(`[data-tour="${target}"]`);
  if (!el || el.offsetParent === null) return null; // скрыт (например, сайдбар на телефоне)
  const r = el.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) return null;
  return { top: r.top, left: r.left, width: r.width, height: r.height };
}

export function OnboardingTour({ me }: { me: Me }) {
  const [active, setActive] = useState(false);
  const [step, setStep] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);
  const steps = buildSteps(me);

  const finish = useCallback(() => {
    setActive(false);
    localStorage.setItem(TOUR_DONE_KEY, "1");
  }, []);

  // Первый вход — показываем обучение один раз; повторно — по событию.
  useEffect(() => {
    const start = () => { setStep(0); setActive(true); };
    if (!localStorage.getItem(TOUR_DONE_KEY)) {
      const t = setTimeout(start, 900);
      window.addEventListener(TOUR_START_EVENT, start);
      return () => { clearTimeout(t); window.removeEventListener(TOUR_START_EVENT, start); };
    }
    window.addEventListener(TOUR_START_EVENT, start);
    return () => window.removeEventListener(TOUR_START_EVENT, start);
  }, []);

  // Пересчёт позиции подсветки на каждом шаге и при ресайзе.
  useEffect(() => {
    if (!active) return;
    const update = () => setRect(targetRect(steps[step]?.target));
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, step]);

  if (!active || steps.length === 0) return null;
  const current = steps[step];
  const last = step === steps.length - 1;

  // Позиция карточки: под элементом, если не влезает — над ним; без цели — по центру.
  const cardWidth = Math.min(340, typeof window !== "undefined" ? window.innerWidth - 24 : 340);
  let cardStyle: React.CSSProperties;
  if (rect) {
    const below = rect.top + rect.height + 12;
    const fitsBelow = typeof window === "undefined" || below + 200 < window.innerHeight;
    cardStyle = {
      position: "fixed",
      top: fitsBelow ? below : undefined,
      bottom: fitsBelow ? undefined : window.innerHeight - rect.top + 12,
      left: Math.max(12, Math.min(rect.left, (typeof window !== "undefined" ? window.innerWidth : 0) - cardWidth - 12)),
      width: cardWidth,
    };
  } else {
    cardStyle = {
      position: "fixed",
      left: "50%",
      bottom: 24,
      transform: "translateX(-50%)",
      width: cardWidth,
    };
  }

  return (
    <div className="fixed inset-0 z-[200]" role="dialog" aria-modal="true" aria-label="Обучение по системе">
      {/* затемнение с «окном» вокруг подсвеченного элемента */}
      {rect ? (
        <div
          className="pointer-events-none fixed rounded-lg transition-all duration-300"
          style={{
            top: rect.top - 6, left: rect.left - 6,
            width: rect.width + 12, height: rect.height + 12,
            boxShadow: "0 0 0 9999px rgba(0,0,0,0.55)",
          }}
        />
      ) : (
        <div className="fixed inset-0 bg-black/55" />
      )}

      <div style={cardStyle}
        className="animate-fade-up rounded-xl border bg-[var(--card)] p-4 shadow-2xl">
        <div className="flex items-start justify-between gap-3">
          <div className="text-[15px] font-semibold">{current.title}</div>
          <button onClick={finish} aria-label="Закрыть обучение"
            className="-mr-1 -mt-1 flex size-7 items-center justify-center rounded-md text-[var(--muted-foreground)] hover:bg-[var(--muted)]">
            <X className="size-4" />
          </button>
        </div>
        <p className="mt-1.5 text-sm text-[var(--muted-foreground)]">{current.text}</p>
        <div className="mt-4 flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            {steps.map((_, i) => (
              <span key={i} className={cn(
                "size-1.5 rounded-full transition-colors",
                i === step ? "bg-[var(--primary)]" : "bg-[var(--border)]"
              )} />
            ))}
          </div>
          <div className="flex gap-2">
            {step > 0 && (
              <Button size="sm" variant="outline" onClick={() => setStep(step - 1)}>Назад</Button>
            )}
            <Button size="sm" onClick={() => (last ? finish() : setStep(step + 1))}>
              {last ? "Понятно" : "Далее"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
