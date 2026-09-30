import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import type { DispatchSource, DispatchSources, SourceAnswers } from "@/lib/loader";
import { ShipmentSourcesSheet } from "./shipment-sources-sheet";

/** Ответ GET dispatch-sources из спеки §3.1: на «Мельнице 2» мешков Д1с нет. */
const TWO: DispatchSources = {
  choose: true,
  warehouses: [
    { id: 1, name: "Мельница" },
    { id: 2, name: "Мельница 2" },
  ],
  products: [
    { product: 12, label: "Д1с · Красный 50 кг", color: "Red", bags: 20, short: { "2": 0 } },
    { product: 15, label: "Б · Синий 25 кг", color: "Blue", bags: 40, short: {} },
  ],
};

const THREE: DispatchSources = {
  choose: true,
  warehouses: [...TWO.warehouses, { id: 3, name: "Мельница 3" }],
  products: [TWO.products[0]],
};

/** Д1с: 12 с «Мельницы» и 8 с «Мельницы 2»; Б целиком с «Мельницы 2». */
const COMPLETE: SourceAnswers = { 12: { 1: 12, 2: 8 }, 15: { 2: 40 } };
const RESTART_NOTICE = "Состав заказа или склады изменились — ответьте заново";

type SheetProps = ComponentProps<typeof ShipmentSourcesSheet>;

/** Ответы хранит страница — обёртка держит их в состоянии, как page.tsx. */
function Harness({ initial, ...props }: Omit<SheetProps, "answers"> & { initial: SourceAnswers }) {
  const [answers, setAnswers] = useState(initial);
  return (
    <ShipmentSourcesSheet
      {...props}
      answers={answers}
      onAnswers={(next) => {
        setAnswers(next);
        props.onAnswers(next);
      }}
    />
  );
}

function setup({
  context = TWO,
  initial = {},
  busy = false,
  error = "",
  notice = "",
}: { context?: DispatchSources; initial?: SourceAnswers; busy?: boolean; error?: string; notice?: string } = {}) {
  const user = userEvent.setup();
  const onAnswers = vi.fn<(answers: SourceAnswers) => void>();
  const onConfirm = vi.fn<(sources: DispatchSource[]) => void>();
  const onClose = vi.fn();
  render(
    <Harness
      context={context}
      initial={initial}
      busy={busy}
      error={error}
      notice={notice}
      onAnswers={onAnswers}
      onConfirm={onConfirm}
      onClose={onClose}
    />,
  );
  return { user, onAnswers, onConfirm, onClose };
}

/** Лист без обёртки: страница сама меняет ответы, ошибку и notice (rerender). */
function sheet(props: Partial<SheetProps>) {
  return (
    <ShipmentSourcesSheet
      context={TWO}
      answers={{}}
      onAnswers={vi.fn()}
      busy={false}
      error=""
      notice=""
      onClose={vi.fn()}
      onConfirm={vi.fn()}
      {...props}
    />
  );
}

describe("ShipmentSourcesSheet", () => {
  it("спрашивает по товару без предвыбора, касание записывает ответ и ведёт дальше", async () => {
    const { user, onAnswers } = setup();

    const dialog = screen.getByRole("dialog", { name: "С какого склада?" });
    expect(within(dialog).getByText("1 из 2")).toBeInTheDocument();
    expect(within(dialog).getByText("Д1с · Красный 50 кг")).toBeInTheDocument();
    expect(within(dialog).getByText("20 мешков")).toBeInTheDocument();
    expect(within(dialog).queryAllByRole("button", { pressed: true })).toHaveLength(0);
    expect(within(dialog).getByRole("button", { name: "Мельница", pressed: false })).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Назад" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Мельница 2" }));

    expect(onAnswers).toHaveBeenLastCalledWith({ 12: { 2: 20 } });
    expect(screen.getByText("2 из 2")).toBeInTheDocument();
    expect(screen.getByText("Б · Синий 25 кг")).toBeInTheDocument();
    expect(screen.queryAllByRole("button", { pressed: true })).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Назад" }));

    expect(screen.getByText("1 из 2")).toBeInTheDocument();
    // Подсвечен прошлый ответ самого грузчика — это не предвыбор.
    expect(screen.getByRole("button", { name: "Мельница 2", pressed: true })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Мельница", pressed: false })).toBeInTheDocument();
  });

  it("показывает нехватку только у склада, где мешков меньше, чем в заказе", async () => {
    const { user } = setup();

    expect(screen.getByRole("button", { name: "Мельница 2" })).toHaveAccessibleDescription(
      "осталось 0 — уйдёт в минус",
    );
    expect(screen.getByRole("button", { name: "Мельница" })).not.toHaveAccessibleDescription();
    expect(screen.getAllByText("осталось 0 — уйдёт в минус")).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Мельница" }));

    // У «Б» мешков хватает на обоих складах — остатков не видно.
    expect(screen.getByText("Б · Синий 25 кг")).toBeInTheDocument();
    expect(screen.queryByText(/уйдёт в минус/)).not.toBeInTheDocument();
  });

  it("разбивка: цифры идут в первый склад, остаток — на последний", async () => {
    const { user, onAnswers } = setup();
    await user.click(screen.getByRole("button", { name: "С двух складов…" }));

    expect(screen.getByRole("dialog", { name: "Сколько с каждого склада?" })).toBeInTheDocument();
    expect(screen.getByText("1 из 2")).toBeInTheDocument();
    expect(screen.getByText("Мельница 2 — 20 · остаток")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Дальше" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Цифра 1" }));
    await user.click(screen.getByRole("button", { name: "Цифра 2" }));

    expect(screen.getByLabelText("Мельница")).toHaveValue("12");
    expect(screen.getByText("Мельница 2 — 8 · остаток")).toBeInTheDocument();
    // На «Мельнице 2» мешков нет: 8 уйдут в минус, но отгрузку это не блокирует.
    expect(screen.getByText("осталось 0 — уйдёт в минус")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Дальше" }));

    expect(onAnswers).toHaveBeenLastCalledWith({ 12: { 1: 12, 2: 8 } });
    expect(screen.getByRole("dialog", { name: "С какого склада?" })).toBeInTheDocument();
    expect(screen.getByText("2 из 2")).toBeInTheDocument();
  });

  it("три склада: «С нескольких складов…», перебор выключает «Дальше»", async () => {
    const { user, onAnswers } = setup({ context: THREE });
    await user.click(screen.getByRole("button", { name: "С нескольких складов…" }));

    await user.click(screen.getByRole("button", { name: "Цифра 1" }));
    await user.click(screen.getByRole("button", { name: "Цифра 5" }));
    await user.click(screen.getByLabelText("Мельница 2"));
    await user.click(screen.getByRole("button", { name: "Цифра 1" }));
    await user.click(screen.getByRole("button", { name: "Цифра 0" }));

    expect(screen.getByLabelText("Мельница")).toHaveValue("15");
    expect(screen.getByLabelText("Мельница 2")).toHaveValue("10");
    expect(screen.getByText("Больше, чем в заказе: 20")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Дальше" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Стереть" }));
    await user.click(screen.getByRole("button", { name: "Стереть" }));
    await user.click(screen.getByRole("button", { name: "Цифра 5" }));

    expect(screen.queryByText("Больше, чем в заказе: 20")).not.toBeInTheDocument();
    expect(screen.getByText("Мельница 3 — 0 · остаток")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Дальше" }));

    // Нулевой остаток на «Мельницу 3» не пишется.
    expect(onAnswers).toHaveBeenLastCalledWith({ 12: { 1: 15, 2: 5 } });
    expect(screen.getByRole("dialog", { name: "Проверьте и отгрузите" })).toBeInTheDocument();
  });

  it("сводка: склады по товарам, «Изменить» возвращает к шагу, отгрузка отдаёт плоский список", async () => {
    const { user, onConfirm } = setup({ initial: COMPLETE });

    // Ответы уже есть (лист открыт снова) — сразу сводка.
    expect(screen.getByRole("dialog", { name: "Проверьте и отгрузите" })).toBeInTheDocument();
    const first = screen.getByRole("region", { name: "Д1с · Красный 50 кг" });
    expect(within(first).getByText("Мельница — 12, Мельница 2 — 8")).toBeInTheDocument();
    expect(within(first).getByText("Мельница 2: осталось 0 — уйдёт в минус")).toBeInTheDocument();
    const second = screen.getByRole("region", { name: "Б · Синий 25 кг" });
    expect(within(second).getByText("со склада: Мельница 2")).toBeInTheDocument();
    expect(within(second).queryByText(/уйдёт в минус/)).not.toBeInTheDocument();

    await user.click(within(second).getByRole("button", { name: "Изменить" }));

    expect(screen.getByRole("dialog", { name: "С какого склада?" })).toBeInTheDocument();
    expect(screen.getByText("2 из 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Мельница 2", pressed: true })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Мельница" }));

    const changed = screen.getByRole("region", { name: "Б · Синий 25 кг" });
    expect(within(changed).getByText("со склада: Мельница")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Отгрузить · 60 мешков" }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    const sent = [...onConfirm.mock.calls[0][0]].sort((a, b) => a.product - b.product || a.warehouse - b.warehouse);
    expect(sent).toEqual([
      { product: 12, warehouse: 1, bags: 12 },
      { product: 12, warehouse: 2, bags: 8 },
      { product: 15, warehouse: 1, bags: 40 },
    ]);
  });

  it("ошибка и notice — внутри листа; сброшенные страницей ответы возвращают к первому товару", () => {
    const { rerender } = render(sheet({ answers: COMPLETE, error: "Нет связи с сервером" }));

    const dialog = screen.getByRole("dialog", { name: "Проверьте и отгрузите" });
    const alert = within(dialog).getByRole("alert");
    expect(alert).toHaveTextContent("Нет связи с сервером");
    // Над кнопками внизу, а не в прокручиваемом теле.
    expect(document.querySelector("[data-modal-scroll-body]")).not.toContainElement(alert);

    rerender(sheet({ answers: {}, notice: RESTART_NOTICE }));

    const restarted = screen.getByRole("dialog", { name: "С какого склада?" });
    expect(within(restarted).getByText("1 из 2")).toBeInTheDocument();
    expect(within(restarted).getByRole("status")).toHaveTextContent(RESTART_NOTICE);
    expect(within(restarted).queryAllByRole("button", { pressed: true })).toHaveLength(0);
    expect(within(restarted).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ответы сброшены посреди опроса — лист тоже возвращается к первому товару", () => {
    const { rerender } = render(sheet({ answers: { 12: { 2: 20 } } }));

    expect(screen.getByText("2 из 2")).toBeInTheDocument();

    rerender(sheet({ answers: {} }));

    expect(screen.getByText("1 из 2")).toBeInTheDocument();
    expect(screen.getByText("Д1с · Красный 50 кг")).toBeInTheDocument();
  });

  it("один склад — без «С двух складов…»: разбивать не на что", () => {
    setup({ context: { choose: false, warehouses: [TWO.warehouses[0]], products: [TWO.products[0]] } });

    expect(screen.getByRole("button", { name: "Мельница" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /складов…/ })).not.toBeInTheDocument();
  });

  it("пока отгружаем — «Отгружаем…», кнопки выключены", () => {
    setup({ initial: COMPLETE, busy: true });

    expect(screen.getByRole("button", { name: "Отгружаем…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Назад" })).toBeDisabled();
    for (const edit of screen.getAllByRole("button", { name: "Изменить" })) expect(edit).toBeDisabled();
  });

  it("закрывается только крестиком", async () => {
    const { user, onClose } = setup();

    await user.keyboard("{Escape}");
    expect(onClose).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Закрыть" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
