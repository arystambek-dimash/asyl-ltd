import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RailPreview } from "@/lib/rail-report";
import { RailReportSheet } from "./rail-report-sheet";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  apiUrls: [] as (string | null)[],
  options: {
    products: [{ id: 5, label: "Мука высший сорт · Красный 50 кг", weight_kg: "50.00" }],
    clients: [{ id: 9, name: "ООО OSIYO NAV NIHOL", currency: "USD", department_name: "Экспорт" }],
  },
}));

vi.mock("@/lib/api", () => ({
  api: { post: mocks.post },
  apiError: (error: Error) => error.message,
}));
vi.mock("@/lib/use-api", () => ({
  useApi: (url: string | null) => {
    mocks.apiUrls.push(url);
    return { data: url ? mocks.options : null, error: "", loading: false, reload: vi.fn() };
  },
}));

const REPORT = "сб 19.09.26 Узбекистан ООО OSIYO NAV NIHOL\nСт. Раустан 1 вагон\nД1с-28087658-68 тн";

function preview(fields: Partial<RailPreview> = {}): RailPreview {
  return {
    order_id: null,
    day: "2026-09-19",
    country: "Узбекистан",
    client_name: "ООО OSIYO NAV NIHOL",
    station: "Раустан",
    declared_wagons: 1,
    client: { id: 9, name: "ООО OSIYO NAV NIHOL", profile: false },
    currency: "USD",
    wagons: [
      {
        position: 1,
        line: 3,
        number: "28087658",
        number_status: "ok",
        code: "Д1с",
        product_id: 5,
        product_label: "Мука высший сорт · Красный 50 кг",
        tons: "68",
        bags: 1360,
        unit_price: "7.50",
        amount: "10200.00",
      },
    ],
    items: [],
    totals: { wagons: 1, tons: "68", bags: 1360, amount: "10200.00", currency: "USD" },
    issues: [],
    warnings: [],
    unresolved: { client: "", products: [] },
    shippable_orders: [],
    ok: true,
    can_apply: true,
    can_remember_products: true,
    can_remember_clients: true,
    ...fields,
  };
}

const unknownProduct = () =>
  preview({
    ok: false,
    can_apply: false,
    wagons: [
      { ...preview().wagons[0], product_id: null, product_label: "", bags: null, unit_price: null, amount: null },
    ],
    issues: [
      { code: "product_unknown", message: "Неизвестный код товара «Д1с»", line: 3, subject: "Д1с", order_id: null },
    ],
    unresolved: { client: "", products: ["Д1с"] },
  });

/** Похоже, отчёт уже внесли руками: ``ids`` — похожие заказы, ``shippable`` — какие ещё ждут отгрузки. */
const manualDuplicates = (ids: number[], shippable: number[]) =>
  preview({
    ok: false,
    can_apply: false,
    issues: ids.map((id) => ({
      code: "manual_order_duplicate",
      message: `Похоже, этот отчёт уже внесён вручную: заказ №${id}`,
      line: null,
      subject: "",
      order_id: id,
    })),
    shippable_orders: shippable,
  });

async function paste(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByLabelText("Текст отчёта"));
  await user.paste(REPORT);
}

describe("RailReportSheet", () => {
  beforeEach(() => {
    mocks.post.mockReset();
    mocks.apiUrls = [];
  });

  it("проверяет отчёт без записи и проводит его, отдавая строку истории экрану", async () => {
    const user = userEvent.setup();
    const onApplied = vi.fn();
    const row = { id: 77, status: "shipped" };
    mocks.post.mockResolvedValueOnce({ data: preview() }).mockResolvedValueOnce({ data: row });
    render(<RailReportSheet onClose={vi.fn()} onApplied={onApplied} />);

    await paste(user);
    await user.click(screen.getByRole("button", { name: "Проверить" }));

    expect(mocks.post).toHaveBeenLastCalledWith("/loader/rail-report/preview/", { text: REPORT });
    const table = await screen.findByRole("table", { name: "Вагоны отчёта" });
    expect(within(table).getByText("28087658")).toBeInTheDocument();
    expect(within(table).getByLabelText("номер верный")).toBeInTheDocument();
    expect(screen.getByText("ст. Раустан")).toBeInTheDocument();
    expect(screen.getByText("Всё сошлось — можно проводить.")).toBeInTheDocument();
    // Пока ничего не нужно разрешать, справочники не грузятся.
    expect(mocks.apiUrls.every((url) => url === null)).toBe(true);

    await user.click(screen.getByRole("button", { name: "Провести" }));

    expect(mocks.post).toHaveBeenLastCalledWith("/loader/rail-report/apply/", { text: REPORT });
    expect(onApplied).toHaveBeenCalledWith(row);
  });

  it("журнал WhatsApp-бота: текст сообщения проверяется сразу, адреса — сообщения", async () => {
    const user = userEvent.setup();
    const onApplied = vi.fn();
    const row = { id: 7, status: "applied" };
    mocks.post.mockResolvedValueOnce({ data: unknownProduct() }).mockResolvedValueOnce({ data: preview() });
    mocks.post.mockResolvedValueOnce({ data: row });
    render(
      <RailReportSheet
        api="/bots/whatsapp/messages/7"
        initialText={REPORT}
        title="Провести сообщение"
        onClose={vi.fn()}
        onApplied={onApplied}
      />,
    );

    expect(screen.getByLabelText("Текст отчёта")).toHaveValue(REPORT);
    expect(await screen.findByText("Неизвестный код товара «Д1с»")).toBeInTheDocument();
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.post).toHaveBeenLastCalledWith("/bots/whatsapp/messages/7/preview/", { text: REPORT });
    expect(mocks.apiUrls).toContain("/bots/whatsapp/messages/7/options/");

    await user.selectOptions(screen.getByLabelText("«Д1с»"), "5");
    await user.click(screen.getByRole("button", { name: "Запомнить" }));
    expect(mocks.post).toHaveBeenLastCalledWith("/bots/whatsapp/messages/7/product-codes/", {
      text: REPORT,
      code: "Д1с",
      product: 5,
    });

    await user.click(await screen.findByRole("button", { name: "Провести" }));
    expect(mocks.post).toHaveBeenLastCalledWith("/bots/whatsapp/messages/7/apply/", { text: REPORT });
    expect(onApplied).toHaveBeenCalledWith(row);
  });

  it("запоминает неизвестный код товара и применяет свежий предпросмотр из ответа", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValueOnce({ data: unknownProduct() }).mockResolvedValueOnce({ data: preview() });
    render(<RailReportSheet onClose={vi.fn()} onApplied={vi.fn()} />);
    await paste(user);
    await user.click(screen.getByRole("button", { name: "Проверить" }));

    expect(await screen.findByText("Неизвестный код товара «Д1с»")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Провести" })).toBeDisabled();
    expect(mocks.apiUrls).toContain("/loader/rail-report/options/");

    await user.selectOptions(screen.getByLabelText("«Д1с»"), "5");
    await user.click(screen.getByRole("button", { name: "Запомнить" }));

    expect(mocks.post).toHaveBeenLastCalledWith("/loader/rail-report/product-codes/", {
      text: REPORT,
      code: "Д1с",
      product: 5,
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "Провести" })).toBeEnabled());
    expect(screen.queryByText("Неизвестный код товара «Д1с»")).not.toBeInTheDocument();
  });

  it("без прав на словарь объясняет, кто может запомнить код", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValueOnce({
      data: { ...unknownProduct(), can_remember_products: false, can_remember_clients: false },
    });
    render(<RailReportSheet onClose={vi.fn()} onApplied={vi.fn()} />);
    await paste(user);
    await user.click(screen.getByRole("button", { name: "Проверить" }));

    expect(
      await screen.findByText("Код товара запоминает сотрудник, который правит товары или создаёт заказы."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Запомнить" })).not.toBeInTheDocument();
    expect(mocks.apiUrls.every((url) => url === null)).toBe(true);
  });

  it("выбирает клиента и валюту для незнакомого названия", async () => {
    const user = userEvent.setup();
    mocks.post
      .mockResolvedValueOnce({
        data: preview({
          ok: false,
          can_apply: false,
          client: null,
          currency: "",
          client_name: "OSIYO Ташкент",
          issues: [{ code: "client_unknown", message: "Клиент не найден", line: null, subject: "", order_id: null }],
          unresolved: { client: "OSIYO Ташкент", products: [] },
        }),
      })
      .mockResolvedValueOnce({ data: preview({ currency: "KZT" }) });
    render(<RailReportSheet onClose={vi.fn()} onApplied={vi.fn()} />);
    await paste(user);
    await user.click(screen.getByRole("button", { name: "Проверить" }));

    await user.selectOptions(await screen.findByLabelText("Клиент"), "9");
    expect(screen.getByLabelText("Валюта")).toHaveValue("USD");
    await user.selectOptions(screen.getByLabelText("Валюта"), "KZT");
    await user.click(screen.getByRole("button", { name: "Запомнить" }));

    expect(mocks.post).toHaveBeenLastCalledWith("/loader/rail-report/client-names/", {
      text: REPORT,
      client_name: "OSIYO Ташкент",
      client: 9,
      currency: "KZT",
    });
    expect(await screen.findByText("KZT")).toBeInTheDocument();
  });

  it("показывает ошибку «Провести» внутри листа и перепроверяет отчёт", async () => {
    const user = userEvent.setup();
    const duplicate = preview({
      ok: false,
      can_apply: false,
      issues: [
        { code: "wagon_already_shipped", message: "Вагон 28087658 уже отгружен", line: 3, subject: "", order_id: 5 },
      ],
    });
    mocks.post
      .mockResolvedValueOnce({ data: preview() })
      .mockRejectedValueOnce(new Error("Отчёт нужно проверить"))
      .mockResolvedValueOnce({ data: duplicate });
    const onApplied = vi.fn();
    render(<RailReportSheet onClose={vi.fn()} onApplied={onApplied} />);
    await paste(user);
    await user.click(screen.getByRole("button", { name: "Проверить" }));
    await user.click(await screen.findByRole("button", { name: "Провести" }));

    const error = await screen.findByText("Отчёт нужно проверить");
    // Над кнопками внизу, а не в прокручиваемом теле: длинный отчёт не уводит ошибку из вида.
    expect(document.querySelector("[data-modal-scroll-body]")).not.toContainElement(error);
    expect(await screen.findByText("Вагон 28087658 уже отгружен")).toBeInTheDocument();
    expect(mocks.post).toHaveBeenLastCalledWith("/loader/rail-report/preview/", { text: REPORT });
    expect(onApplied).not.toHaveBeenCalled();
  });

  it("показывает ошибку «Запомнить» в разделе кода товара", async () => {
    const user = userEvent.setup();
    mocks.post
      .mockResolvedValueOnce({ data: unknownProduct() })
      .mockRejectedValueOnce(new Error("Код «Д1с» уже у товара «Мука первый сорт»"));
    render(<RailReportSheet onClose={vi.fn()} onApplied={vi.fn()} />);
    await paste(user);
    await user.click(screen.getByRole("button", { name: "Проверить" }));

    await user.selectOptions(await screen.findByLabelText("«Д1с»"), "5");
    await user.click(screen.getByRole("button", { name: "Запомнить" }));

    const section = screen.getByRole("region", { name: "Неизвестные коды товара" });
    expect(await within(section).findByText("Код «Д1с» уже у товара «Мука первый сорт»")).toBeInTheDocument();
  });

  it("отгружает похожий ручной заказ по отчёту вместо нового", async () => {
    const user = userEvent.setup();
    const duplicate = manualDuplicates([7], [7]);
    mocks.post
      .mockResolvedValueOnce({ data: duplicate })
      .mockResolvedValueOnce({ data: preview({ order_id: 7 }) })
      .mockResolvedValueOnce({ data: duplicate });
    render(<RailReportSheet onClose={vi.fn()} onApplied={vi.fn()} />);
    await paste(user);
    await user.click(screen.getByRole("button", { name: "Проверить" }));

    await user.click(await screen.findByRole("button", { name: "Отгрузить заказ №7 по этому отчёту" }));

    expect(mocks.post).toHaveBeenLastCalledWith("/loader/rail-report/preview/", { text: REPORT, order: 7 });
    expect(await screen.findByRole("button", { name: "Отгрузить по отчёту" })).toBeEnabled();
    expect(screen.getByText("Вагоны · заказ №7")).toBeInTheDocument();

    // Передумали — обратно к новому заказу по тому же тексту.
    await user.click(screen.getByRole("button", { name: "← Провести отчёт новым заказом" }));

    expect(mocks.post).toHaveBeenLastCalledWith("/loader/rail-report/preview/", { text: REPORT });
    expect(await screen.findByText("Вагоны · отчёт из WhatsApp")).toBeInTheDocument();
  });

  it("уже отгруженный ручной заказ — только предупреждение, без «Отгрузить по отчёту»", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValueOnce({ data: manualDuplicates([7], []) });
    render(<RailReportSheet onClose={vi.fn()} onApplied={vi.fn()} />);
    await paste(user);
    await user.click(screen.getByRole("button", { name: "Проверить" }));

    expect(await screen.findByText("Похоже, этот отчёт уже внесён вручную: заказ №7")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Отгрузить заказ №7/ })).not.toBeInTheDocument();
  });

  it("отгружает заранее внесённый заказ и сбрасывает предпросмотр при правке текста", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValueOnce({ data: preview({ order_id: 42 }) });
    render(<RailReportSheet orderId={42} onClose={vi.fn()} onApplied={vi.fn()} />);
    await paste(user);
    await user.click(screen.getByRole("button", { name: "Проверить" }));

    expect(mocks.post).toHaveBeenLastCalledWith("/loader/rail-report/preview/", { text: REPORT, order: 42 });
    expect(await screen.findByRole("button", { name: "Отгрузить по отчёту" })).toBeEnabled();

    await user.type(screen.getByLabelText("Текст отчёта"), " ");

    expect(screen.queryByRole("button", { name: "Отгрузить по отчёту" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Проверить" })).toBeEnabled();
  });
});
