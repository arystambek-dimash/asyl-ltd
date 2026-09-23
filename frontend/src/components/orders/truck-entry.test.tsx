import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TransportQueueRow } from "@/lib/types";
import { TruckEntrySection } from "./truck-entry";

const mocks = vi.hoisted(() => ({
  rows: [] as TransportQueueRow[],
  urls: [] as string[],
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { get: mocks.get, post: mocks.post },
  apiError: (cause: Error) => cause.message,
  isCanceledRequest: () => false,
}));

const row = (id: number, fields: Partial<TransportQueueRow> = {}): TransportQueueRow => ({
  id,
  client: id,
  client_name: `Клиент ${id}`,
  client_country: "Казахстан",
  status: "confirmed",
  arrival_date: null,
  created_at: "2026-09-23T10:00:00+05:00",
  planned_on: "2026-09-23",
  bags: 1360,
  truck_number: "",
  trailer_number: "",
  transport_locked: false,
  transport_suggestions: [],
  plate_warning: null,
  ...fields,
});

describe("«Фуры»: быстрый ввод номеров", () => {
  beforeEach(() => {
    mocks.rows = [row(101), row(102)];
    mocks.urls = [];
    mocks.post.mockReset();
    mocks.get.mockReset().mockImplementation(async (url: string) => {
      mocks.urls.push(url);
      return { data: mocks.rows };
    });
  });

  it("Enter ведёт от тягача к прицепу, а из прицепа сохраняет строку и переходит к следующей фуре", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValue({
      data: row(101, { truck_number: "CLIENT777", trailer_number: "07KG837PB", plate_warning: "Номер не похож" }),
    });
    render(<TruckEntrySection />);

    await user.type(await screen.findByLabelText("Тягач №101"), "client777{Enter}");
    expect(screen.getByLabelText("Прицеп №101")).toHaveFocus();
    await user.type(screen.getByLabelText("Прицеп №101"), "07 kg 837 pb{Enter}");

    expect(mocks.post).toHaveBeenCalledWith("/orders/101/transport/", {
      truck_number: "CLIENT777",
      trailer_number: "07KG837PB",
    });
    expect(screen.getByLabelText("Тягач №102")).toHaveFocus();
    // Ответ POST применяется к строке: сохранено и мягкое предупреждение сервера.
    expect(await screen.findByText("Сохранено")).toBeInTheDocument();
    expect(screen.getByText("Номер не похож")).toBeInTheDocument();
    expect(screen.getByLabelText("Тягач №101")).toHaveValue("CLIENT777");
    expect(mocks.get).toHaveBeenCalledTimes(1);
  });

  it("Esc откатывает правку строки", async () => {
    const user = userEvent.setup();
    mocks.rows = [row(101, { truck_number: "403BJN13" })];
    render(<TruckEntrySection />);

    const truck = await screen.findByLabelText("Тягач №101");
    await user.clear(truck);
    await user.type(truck, "612bex13");
    expect(truck).toHaveValue("612 BEX 13");
    await user.keyboard("{Escape}");

    expect(truck).toHaveValue("403 BJN 13");
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("строку без изменений не отправляет, но фокус идёт дальше", async () => {
    const user = userEvent.setup();
    render(<TruckEntrySection />);

    await user.click(await screen.findByLabelText("Тягач №101"));
    await user.keyboard("{Enter}{Enter}");

    expect(mocks.post).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Тягач №102")).toHaveFocus();
  });

  it("ошибку показывает под строкой и сохраняет набранное", async () => {
    const user = userEvent.setup();
    mocks.post.mockRejectedValue(new Error("Номер транспорта указал клиент — изменить его может только клиент"));
    render(<TruckEntrySection />);

    await user.type(await screen.findByLabelText("Тягач №101"), "403bjn13{Enter}{Enter}");

    expect(await screen.findByText(/изменить его может только клиент/)).toBeInTheDocument();
    expect(screen.getByLabelText("Тягач №101")).toHaveValue("403 BJN 13");
  });

  it("чип «как в прошлый раз» подставляет пару, а сохраняет Enter — промах не уходит клиенту", async () => {
    const user = userEvent.setup();
    mocks.rows = [row(101, { transport_suggestions: [{ truck_number: "07KG695ADT", trailer_number: "07KG837PB" }] })];
    mocks.post.mockResolvedValue({ data: row(101, { truck_number: "07KG695ADT", trailer_number: "07KG837PB" }) });
    render(<TruckEntrySection />);

    await user.click(await screen.findByRole("button", { name: "07 KG 695 ADT / 07 KG 837 PB" }));

    expect(mocks.post).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Тягач №101")).toHaveValue("07 KG 695 ADT");
    expect(screen.getByLabelText("Прицеп №101")).toHaveValue("07 KG 837 PB");
    expect(screen.getByLabelText("Прицеп №101")).toHaveFocus();
    expect(screen.getByText("Не сохранено")).toBeInTheDocument();
    await user.keyboard("{Enter}");

    expect(mocks.post).toHaveBeenCalledWith("/orders/101/transport/", {
      truck_number: "07KG695ADT",
      trailer_number: "07KG837PB",
    });
    expect(await screen.findByText("Сохранено")).toBeInTheDocument();
  });

  it("отправляет только исправленный номер: прицеп, которого строка не меняла, не стирается", async () => {
    const user = userEvent.setup();
    // Прицеп дописали с другого места, а тягача ещё нет.
    mocks.rows = [row(101, { trailer_number: "07KG837PB" })];
    mocks.post.mockResolvedValue({ data: row(101, { truck_number: "403BJN13", trailer_number: "07KG837PB" }) });
    render(<TruckEntrySection />);

    await user.type(await screen.findByLabelText("Тягач №101"), "403bjn13{Enter}{Enter}");

    expect(mocks.post).toHaveBeenCalledWith("/orders/101/transport/", { truck_number: "403BJN13" });
  });

  it("набранное без Enter помечено «Не сохранено» и не теряется при «Обновить»", async () => {
    const user = userEvent.setup();
    render(<TruckEntrySection />);

    await user.type(await screen.findByLabelText("Тягач №101"), "403bjn13");
    // Мышью к следующей фуре: строка не сохранена, и это видно.
    await user.click(screen.getByLabelText("Тягач №102"));
    expect(screen.getByText("Не сохранено")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Обновить" }));

    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText("Тягач №101")).toHaveValue("403 BJN 13");
    expect(screen.getByText("Не сохранено")).toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("«Обновить» держит сохранённое, пока не придёт список, а ошибку показывает над списком", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValue({ data: row(101, { truck_number: "403BJN13" }) });
    render(<TruckEntrySection />);
    await user.type(await screen.findByLabelText("Тягач №101"), "403bjn13{Enter}{Enter}");
    expect(await screen.findByText("Сохранено")).toBeInTheDocument();

    mocks.get.mockRejectedValueOnce(new Error("Нет связи с сервером"));
    await user.click(screen.getByRole("button", { name: "Обновить" }));

    expect(await screen.findByText("Нет связи с сервером")).toBeInTheDocument();
    expect(screen.getByLabelText("Тягач №101")).toHaveValue("403 BJN 13");
    expect(screen.getByText("Сохранено")).toBeInTheDocument();

    // Пришёл свежий список — он главнее: номер тем временем поправили с другого места.
    mocks.rows = [row(101, { truck_number: "612BEX13" }), row(102)];
    await user.click(screen.getByRole("button", { name: "Повторить" }));

    await waitFor(() => expect(screen.getByLabelText("Тягач №101")).toHaveValue("612 BEX 13"));
    expect(screen.queryByText("Сохранено")).not.toBeInTheDocument();
    expect(screen.queryByText("Нет связи с сервером")).not.toBeInTheDocument();
  });

  it("номер, указанный клиентом, только для чтения", async () => {
    mocks.rows = [row(101, { truck_number: "403BJN13", transport_locked: true })];
    render(<TruckEntrySection />);

    expect(await screen.findByText("403 BJN 13")).toBeInTheDocument();
    expect(screen.queryByLabelText("Тягач №101")).not.toBeInTheDocument();
    expect(screen.getByText(/Номер указал клиент/)).toBeInTheDocument();
  });

  it("по умолчанию — фуры без номера, отдельно — все на сегодня", async () => {
    const user = userEvent.setup();
    render(<TruckEntrySection />);

    await waitFor(() => expect(mocks.urls.at(-1)).toBe("/orders/transport-queue/?filter=missing"));
    await user.click(screen.getByRole("button", { name: "Все на сегодня" }));
    await waitFor(() => expect(mocks.urls.at(-1)).toBe("/orders/transport-queue/?filter=today"));
  });
});
