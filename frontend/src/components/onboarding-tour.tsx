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
  if (can(me, "payments.confirm") || can(me, "reports.view")) steps.push({
    target: "nav:/accounting",
    title: "Касса",
    text: "Подтверждение заявок и оплат по обоим отделам — деньги учитываются сразу. Вкладка «Долги»: кто и сколько должен, с окнами оплат по расписанию.",
  });
  if (can(me, "warehouse.view")) steps.push({
    target: "nav:/warehouse",
    title: "Склад",
    text: "Остатки готовой продукции. Кнопка «Изменить остаток» — приёмка и списание с предпросмотром «сейчас → станет».",
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

  // Автоматически — ровно один раз (первый вход): флаг ставится сразу при
  // показе, чтобы тур не выскакивал на каждой странице. Дальше — только «?».
  useEffect(() => {
    const start = () => { setStep(0); setActive(true); };
    if (!localStorage.getItem(TOUR_DONE_KEY)) {
      const t = setTimeout(() => {
        localStorage.setItem(TOUR_DONE_KEY, "1");
        start();
      }, 900);
      window.addEventListener(TOUR_START_EVENT, start);
      return () => { clearTimeout(t); window.removeEventListener(TOUR_START_EVENT, start); };
    }
    window.addEventListener(TOUR_START_EVENT, start);
    return () => window.removeEventListener(TOUR_START_EVENT, start);
  }, []);

  // Пересчёт позиции подсветки на каждом шаге, при ресайзе; Esc — выход.
  useEffect(() => {
    if (!active) return;
    const target = steps[step]?.target;
    if (target) {
      document.querySelector(`[data-tour="${target}"]`)
        ?.scrollIntoView({ block: "nearest" });
    }
    const update = () => setRect(targetRect(target));
    update();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") finish(); };
    window.addEventListener("resize", update);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("keydown", onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, step]);

  if (!active || steps.length === 0) return null;
  const current = steps[step];
  const last = step === steps.length - 1;

  // Позиция карточки: снизу → сверху → справа от элемента; координаты всегда
  // зажимаются в видимую область, чтобы карточка не «улетала» за экран
  // (например, у сайдбара высота во весь экран — «снизу» не существует).
  const vw = typeof window !== "undefined" ? window.innerWidth : 1200;
  const vh = typeof window !== "undefined" ? window.innerHeight : 800;
  const cardWidth = Math.min(340, vw - 24);
  const cardH = 220; // оценка высоты карточки для расчёта, ниже всё зажимается
  const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(v, hi));
  let cardStyle: React.CSSProperties;
  if (rect) {
    let top: number;
    let left = rect.left;
    if (rect.top + rect.height + 12 + cardH < vh) {
      top = rect.top + rect.height + 12;                    // снизу
    } else if (rect.top - cardH - 12 > 0) {
      top = rect.top - cardH - 12;                          // сверху
    } else {
      top = rect.top + rect.height / 2 - cardH / 2;         // сбоку по центру
      left = rect.left + rect.width + 12;
    }
    cardStyle = {
      position: "fixed",
      top: clamp(top, 12, vh - cardH - 12),
      left: clamp(left, 12, vw - cardWidth - 12),
      width: cardWidth,
    };
  } else {
    // Без transform: его перебила бы анимация появления (animate-fade-up).
    cardStyle = {
      position: "fixed",
      left: Math.round((vw - cardWidth) / 2),
      bottom: 24,
      width: cardWidth,
    };
  }

  return (
    <div className="fixed inset-0 z-[200]" role="dialog" aria-modal="true"
      aria-label="Обучение по системе"
      onClick={finish}>
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

      <div style={cardStyle} onClick={(e) => e.stopPropagation()}
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
