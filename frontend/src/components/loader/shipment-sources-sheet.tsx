"use client";
import { useId, useState, type ReactNode } from "react";
import { LoaderCircle, PackageCheck } from "lucide-react";
import { ColorDot } from "@/components/monoblock/ui";
import { Button } from "@/components/ui/button";
import { DepartmentDot } from "@/components/ui/department-badge";
import { FormError } from "@/components/ui/data-state";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Numpad } from "@/components/ui/numpad";
import {
  answersComplete,
  dispatchSourcesPayload,
  shortageNote,
  sourcesText,
  splitLabel,
  splitRemainder,
  warehouseTone,
  type DispatchSource,
  type DispatchSourceProduct,
  type DispatchSources,
  type DispatchWarehouse,
  type SourceAnswers,
} from "@/lib/loader";
import { colorMeta } from "@/lib/monoblock-colors";
import { eraseAmount, pressAmountDigit } from "@/lib/payment-amount";
import { bagsLabel, cn } from "@/lib/utils";

/** Мешки товара по складам: id склада → мешков. */
type Chosen = Record<number, number>;

/**
 * Шаг опросника: кнопки складов товара, разбивка товара по складам (черновик
 * Numpad по каждому складу, кроме последнего, и склад, куда идут цифры) или сводка.
 */
type Step =
  | { kind: "product"; index: number }
  | { kind: "split"; index: number; draft: Record<number, string>; focused: number }
  | { kind: "summary" };

/** Рамка своего прошлого ответа грузчика и поля, куда идут цифры. Это не предвыбор. */
const PICKED = "border-2 border-[var(--primary)] bg-[var(--accent)]";
/** То же для кнопки склада: она залита своим цветом, поэтому прошлый ответ — кольцом. */
const PICKED_TONE = "ring-4 ring-[var(--primary)] ring-offset-2 ring-offset-[var(--card)]";

/** Ответ на товар полный: все его мешки разложены по складам листа. */
function productAnswered(context: DispatchSources, answers: SourceAnswers, product: DispatchSourceProduct): boolean {
  return answersComplete({ ...context, products: [product] }, answers);
}

/** Первый товар без ответа; когда отвечены все — сводка. */
function openStep(context: DispatchSources, answers: SourceAnswers): Step {
  const index = context.products.findIndex((product) => !productAnswered(context, answers, product));
  return index === -1 ? { kind: "summary" } : { kind: "product", index };
}

/**
 * Страница сбросила ответы (состав заказа или склады изменились) или прислала
 * другие товары: лист сам возвращается к первому товару без ответа. До шага
 * товара доходят, только ответив на все товары перед ним.
 */
function visibleStep(step: Step, context: DispatchSources, answers: SourceAnswers): Step {
  const reachable =
    step.kind === "summary"
      ? answersComplete(context, answers)
      : step.index < context.products.length &&
        answersComplete({ ...context, products: context.products.slice(0, step.index) }, answers);
  return reachable ? step : openStep(context, answers);
}

/** «Назад»: с разбивки — к складам того же товара, со сводки — к последнему товару; у первого товара назад нет. */
function previousStep(step: Step, count: number): Step | null {
  if (step.kind === "summary") return { kind: "product", index: count - 1 };
  if (step.kind === "split") return { kind: "product", index: step.index };
  return step.index > 0 ? { kind: "product", index: step.index - 1 } : null;
}

/** Разбивка открывается с прошлыми числами грузчика, цифры идут в первый склад. */
function splitStep(context: DispatchSources, answers: SourceAnswers, index: number): Step {
  const previous = answers[context.products[index].product] ?? {};
  const editable = context.warehouses.slice(0, -1);
  return {
    kind: "split",
    index,
    draft: Object.fromEntries(
      editable.map((warehouse) => [warehouse.id, previous[warehouse.id] ? String(previous[warehouse.id]) : ""]),
    ),
    focused: editable[0].id,
  };
}

interface SplitParts {
  /** Склады с полем ввода: все, кроме последнего. */
  editable: DispatchWarehouse[];
  /** Последний склад получает остаток сам. */
  last: DispatchWarehouse;
  remainder: number;
  /** Ответ без нулевых частей. */
  chosen: Chosen;
  /** «Дальше»: что-то введено и не больше, чем в заказе. */
  ready: boolean;
}

function splitParts(
  product: DispatchSourceProduct,
  warehouses: DispatchWarehouse[],
  draft: Record<number, string>,
): SplitParts {
  const editable = warehouses.slice(0, -1);
  const last = warehouses[warehouses.length - 1];
  const parts = editable.map((warehouse) => Number(draft[warehouse.id] || "0"));
  const remainder = splitRemainder(product.bags, parts);
  const chosen: Chosen = {};
  editable.forEach((warehouse, index) => {
    if (parts[index] > 0) chosen[warehouse.id] = parts[index];
  });
  if (remainder > 0) chosen[last.id] = remainder;
  return { editable, last, remainder, chosen, ready: parts.some((part) => part > 0) && remainder >= 0 };
}

/**
 * Опросник «С какого склада?» перед отгрузкой фуры: шаг на товар с большими
 * кнопками складов без предвыбора, разбивка «С двух складов…» через Numpad и
 * сводка с кнопкой «Отгрузить». Ответы хранит страница (`answers`/`onAnswers`):
 * лист, открытый снова с полными ответами, начинается со сводки, а сброшенные
 * ответы возвращают его к первому товару. Остаток склада виден только при
 * нехватке: это предупреждение, не запрет. Ошибка и notice страницы — внутри
 * листа, над кнопками. Закрывается только ✕.
 */
export function ShipmentSourcesSheet({
  context,
  answers,
  onAnswers,
  busy,
  error,
  notice,
  onClose,
  onConfirm,
}: {
  context: DispatchSources;
  answers: SourceAnswers;
  onAnswers: (answers: SourceAnswers) => void;
  busy: boolean;
  error: string;
  notice: string;
  onClose: () => void;
  onConfirm: (sources: DispatchSource[]) => void;
}) {
  const [step, setStep] = useState<Step>(() => openStep(context, answers));
  const current = visibleStep(step, context, answers);
  const count = context.products.length;
  const back = previousStep(current, count);

  function answer(index: number, chosen: Chosen) {
    const next = { ...answers, [context.products[index].product]: chosen };
    onAnswers(next);
    setStep(openStep(context, next));
  }

  function editDraft(change: (value: string) => string) {
    setStep((prev) =>
      prev.kind === "split"
        ? { ...prev, draft: { ...prev.draft, [prev.focused]: change(prev.draft[prev.focused] ?? "") } }
        : prev,
    );
  }

  let title: string;
  let eyebrow: string | undefined;
  let body: ReactNode;
  let action: ReactNode = null;
  if (current.kind === "summary") {
    const total = context.products.reduce((sum, product) => sum + product.bags, 0);
    title = "Проверьте и отгрузите";
    body = (
      <SummaryStep
        context={context}
        answers={answers}
        busy={busy}
        onEdit={(index) => setStep({ kind: "product", index })}
      />
    );
    action = (
      <Button
        className="h-16 flex-1 text-lg"
        disabled={busy || !answersComplete(context, answers)}
        onClick={() => onConfirm(dispatchSourcesPayload(answers))}
      >
        {busy ? <LoaderCircle className="size-6 animate-spin" /> : <PackageCheck className="size-6" />}
        {busy ? "Отгружаем…" : `Отгрузить · ${bagsLabel(total)}`}
      </Button>
    );
  } else {
    const product = context.products[current.index];
    eyebrow = `${current.index + 1} из ${count}`;
    if (current.kind === "product") {
      title = "С какого склада?";
      body = (
        <ProductStep
          context={context}
          product={product}
          answered={productAnswered(context, answers, product) ? answers[product.product] : null}
          onPick={(warehouseId) => answer(current.index, { [warehouseId]: product.bags })}
          onSplit={() => setStep(splitStep(context, answers, current.index))}
        />
      );
    } else {
      const split = splitParts(product, context.warehouses, current.draft);
      title = "Сколько с каждого склада?";
      body = (
        <SplitStep
          product={product}
          split={split}
          draft={current.draft}
          focused={current.focused}
          onFocus={(focused) => setStep((prev) => (prev.kind === "split" ? { ...prev, focused } : prev))}
          onDigit={(digit) => editDraft((value) => pressAmountDigit(value, digit, product.bags))}
          onErase={() => editDraft(eraseAmount)}
        />
      );
      action = (
        <Button
          className="h-14 flex-1 text-base"
          disabled={!split.ready}
          onClick={() => answer(current.index, split.chosen)}
        >
          Дальше
        </Button>
      );
    }
  }

  const footer =
    notice || error || back || action ? (
      <div className="flex flex-1 flex-wrap items-center gap-2">
        {notice && (
          <p role="status" className="basis-full rounded-lg bg-[var(--warning)]/10 px-3 py-2 text-sm font-medium">
            {notice}
          </p>
        )}
        <FormError message={error} className="basis-full" />
        {back && (
          <Button
            variant="outline"
            className={cn("px-5 text-base", current.kind === "summary" ? "h-16" : "h-14", !action && "flex-1")}
            disabled={busy}
            onClick={() => setStep(back)}
          >
            Назад
          </Button>
        )}
        {action}
      </div>
    ) : undefined;

  return (
    <Modal open onClose={onClose} variant="sheet" dismissible={false} eyebrow={eyebrow} title={title} footer={footer}>
      {body}
    </Modal>
  );
}

/** Название товара с точкой цвета мешка. */
function ProductName({ product }: { product: DispatchSourceProduct }) {
  return (
    <div className="flex min-w-0 items-center gap-2 text-base font-semibold">
      <ColorDot className={cn("size-3", colorMeta(product.color).dot)} />
      <span className="min-w-0 break-words">{product.label}</span>
    </div>
  );
}

/** Шапка шага товара: название и крупно — сколько мешков разложить. */
function ProductHeadline({ product }: { product: DispatchSourceProduct }) {
  return (
    <div className="flex flex-col gap-2">
      <ProductName product={product} />
      <div className="text-[40px] font-black leading-none tabular-nums">{bagsLabel(product.bags)}</div>
    </div>
  );
}

function ProductStep({
  context,
  product,
  answered,
  onPick,
  onSplit,
}: {
  context: DispatchSources;
  product: DispatchSourceProduct;
  /** Прошлый ответ самого грузчика («Назад», «Изменить») — подсвечен; без ответа не выбрано ничего. */
  answered: Chosen | null;
  onPick: (warehouseId: number) => void;
  onSplit: () => void;
}) {
  const noteId = useId();
  const picked = answered ? context.warehouses.filter((warehouse) => (answered[warehouse.id] ?? 0) > 0) : [];
  const split = picked.length > 1;
  return (
    <div className="flex flex-col gap-5">
      <ProductHeadline product={product} />
      <div className="flex flex-col gap-3">
        {context.warehouses.map((warehouse) => {
          const note = shortageNote(product, warehouse.id, product.bags);
          const pressed = picked.length === 1 && picked[0].id === warehouse.id;
          const describedBy = `${noteId}-${warehouse.id}`;
          return (
            <div key={warehouse.id} className="flex flex-col gap-1">
              <Button
                aria-pressed={pressed}
                aria-describedby={note ? describedBy : undefined}
                style={{ backgroundColor: warehouseTone(warehouse.id) }}
                className={cn("h-16 w-full text-lg font-semibold text-white hover:opacity-90", pressed && PICKED_TONE)}
                onClick={() => onPick(warehouse.id)}
              >
                {warehouse.name}
              </Button>
              {note && (
                <p id={describedBy} className="px-1 text-sm text-[var(--warning)]">
                  {note}
                </p>
              )}
            </div>
          );
        })}
        {/* Разбивать можно только между двумя складами и больше. */}
        {context.warehouses.length >= 2 && (
          <Button
            variant="ghost"
            aria-pressed={split}
            className={cn("h-14 w-full text-base", split && PICKED)}
            onClick={onSplit}
          >
            {splitLabel(context.warehouses.length)}
          </Button>
        )}
      </div>
    </div>
  );
}

function SplitStep({
  product,
  split,
  draft,
  focused,
  onFocus,
  onDigit,
  onErase,
}: {
  product: DispatchSourceProduct;
  split: SplitParts;
  draft: Record<number, string>;
  /** Склад, куда идут цифры Numpad. */
  focused: number;
  onFocus: (warehouseId: number) => void;
  onDigit: (digit: string) => void;
  onErase: () => void;
}) {
  const fieldId = useId();
  const lastNote = split.remainder > 0 ? shortageNote(product, split.last.id, split.remainder) : "";
  return (
    <div className="flex flex-col gap-4">
      <ProductHeadline product={product} />
      <div className="flex flex-col gap-3">
        {split.editable.map((warehouse) => {
          const id = `${fieldId}-${warehouse.id}`;
          const value = draft[warehouse.id] ?? "";
          const bags = Number(value || "0");
          const note = bags > 0 ? shortageNote(product, warehouse.id, bags) : "";
          return (
            <div key={warehouse.id} className="flex flex-col gap-1">
              <Label htmlFor={id} className="mb-0 flex items-center gap-2 text-sm">
                <DepartmentDot color={warehouseTone(warehouse.id)} className="size-3" />
                {warehouse.name}
              </Label>
              {/* Только чтение: цифры — с Numpad ниже, касание выбирает, куда они идут. */}
              <input
                id={id}
                readOnly
                value={value}
                placeholder="0"
                aria-describedby={note ? `${id}-note` : undefined}
                onFocus={() => onFocus(warehouse.id)}
                className={cn(
                  "h-14 w-full cursor-pointer rounded-xl border bg-[var(--background)] px-4 text-right text-2xl font-bold tabular-nums outline-none",
                  warehouse.id === focused && PICKED,
                )}
              />
              {note && (
                <p id={`${id}-note`} className="px-1 text-sm text-[var(--warning)]">
                  {note}
                </p>
              )}
            </div>
          );
        })}
        <div className="rounded-xl bg-[var(--muted)]/50 px-4 py-3">
          <p className="flex items-center gap-2 text-base font-semibold tabular-nums">
            <DepartmentDot color={warehouseTone(split.last.id)} className="size-3" />
            {`${split.last.name} — ${Math.max(split.remainder, 0)} · остаток`}
          </p>
          {lastNote && <p className="mt-1 text-sm text-[var(--warning)]">{lastNote}</p>}
        </div>
        {split.remainder < 0 && (
          <p className="text-sm font-medium text-[var(--destructive)]">{`Больше, чем в заказе: ${product.bags}`}</p>
        )}
      </div>
      <Numpad onDigit={onDigit} onBackspace={onErase} />
    </div>
  );
}

function SummaryStep({
  context,
  answers,
  busy,
  onEdit,
}: {
  context: DispatchSources;
  answers: SourceAnswers;
  busy: boolean;
  onEdit: (index: number) => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      {context.products.map((product, index) => {
        const chosen = answers[product.product] ?? {};
        return (
          <section
            key={product.product}
            aria-label={product.label}
            className="flex flex-col gap-2 rounded-xl border p-4"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <ProductName product={product} />
                <div className="mt-1 text-lg font-bold tabular-nums">{bagsLabel(product.bags)}</div>
              </div>
              <Button variant="outline" className="h-11 shrink-0" disabled={busy} onClick={() => onEdit(index)}>
                Изменить
              </Button>
            </div>
            <p className="flex flex-wrap items-center gap-2 text-base">
              {context.warehouses
                .filter((warehouse) => (chosen[warehouse.id] ?? 0) > 0)
                .map((warehouse) => (
                  <DepartmentDot key={warehouse.id} color={warehouseTone(warehouse.id)} className="size-3" />
                ))}
              <span>{sourcesText(context, chosen)}</span>
            </p>
            {context.warehouses.map((warehouse) => {
              const bags = chosen[warehouse.id] ?? 0;
              const note = bags > 0 ? shortageNote(product, warehouse.id, bags) : "";
              return note ? (
                <p key={warehouse.id} className="text-sm text-[var(--warning)]">
                  {`${warehouse.name}: ${note}`}
                </p>
              ) : null;
            })}
          </section>
        );
      })}
    </div>
  );
}
