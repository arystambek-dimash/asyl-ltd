import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AxiosError, AxiosHeaders } from "axios";
import { ALL_CLIENTS_STATEMENT_SECTIONS, StatementExportModal } from "@/components/statement-export-modal";
import { monthStartLocalIsoDate, todayLocalIsoDate } from "@/lib/utils";
import { makeDepartment } from "@/test-utils/factories";

const getMock = vi.hoisted(() => vi.fn());
const downloadMock = vi.hoisted(() => vi.fn());
const useApiMock = vi.hoisted(() => vi.fn());

// Разбор ошибок настоящий: скачивание получает тело ошибки Blob-ом.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get: getMock },
}));
vi.mock("@/lib/download", () => ({ downloadBlob: downloadMock }));
vi.mock("@/lib/use-api", () => ({ useApi: useApiMock }));

const departments = [
  makeDepartment({ code: "north", name: "Север", order_count: 5 }),
  makeDepartment({
    id: 2,
    code: "south",
    name: "Юг",
    color: "#1F9D6A",
    is_default: false,
    order_count: 3,
    created_at: "2026-01-02T00:00:00Z",
  }),
];

function renderModal(onClose = vi.fn()) {
  render(
    <StatementExportModal
      open
      onClose={onClose}
      endpoint="/clients/statement/"
      filenameStem="clients-full-statement"
      title="Общая выписка"
      description="Описание"
      scopeLabel="Все клиенты"
      sections={ALL_CLIENTS_STATEMENT_SECTIONS}
    />,
  );
}

describe("StatementExportModal", () => {
  beforeEach(() => {
    getMock.mockReset();
    getMock.mockResolvedValue({ data: new Blob(["xlsx"]) });
    downloadMock.mockReset();
    useApiMock.mockReset();
    useApiMock.mockReturnValue({
      data: departments,
      loading: false,
      error: "",
      reload: vi.fn(),
    });
  });

  it("exports only selected departments and defaults to a visible date period", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderModal(onClose);

    expect(screen.getByLabelText("С даты")).toHaveValue(monthStartLocalIsoDate());
    expect(screen.getByLabelText("По дату")).toHaveValue(todayLocalIsoDate());
    expect(screen.getByRole("button", { name: /Север/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /Юг/ })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: /Юг/ }));
    await user.click(screen.getByRole("button", { name: "Скачать .xlsx" }));

    expect(getMock).toHaveBeenCalledWith("/clients/statement/", {
      params: {
        date_from: monthStartLocalIsoDate(),
        date_to: todayLocalIsoDate(),
        departments: "north",
      },
      responseType: "blob",
    });
    expect(downloadMock).toHaveBeenCalledWith(
      expect.any(Blob),
      `clients-full-statement_${monthStartLocalIsoDate()}_${todayLocalIsoDate()}.xlsx`,
    );
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("keeps an explicit all-time option and dates the downloaded filename", async () => {
    const user = userEvent.setup();
    renderModal();

    await user.click(screen.getByRole("button", { name: "Всё время" }));
    await user.click(screen.getByRole("button", { name: "Скачать .xlsx" }));

    expect(getMock).toHaveBeenCalledWith("/clients/statement/", {
      params: { departments: "north,south" },
      responseType: "blob",
    });
    expect(downloadMock).toHaveBeenCalledWith(
      expect.any(Blob),
      `clients-full-statement_all-time_${todayLocalIsoDate()}.xlsx`,
    );
  });

  it("sends only the sections left selected, in canonical order", async () => {
    const user = userEvent.setup();
    renderModal();

    await user.click(screen.getByRole("button", { name: /Позиции/ }));
    await user.click(screen.getByRole("button", { name: /Платежи/ }));
    await user.click(screen.getByRole("button", { name: "Скачать .xlsx" }));

    expect(getMock.mock.calls[0][1].params.sections).toBe("summary,clients,ledger,orders,debts");
  });

  it("blocks the download when no section is selected", async () => {
    const user = userEvent.setup();
    renderModal();

    await user.click(screen.getByRole("button", { name: "Снять все: разделы" }));

    expect(screen.getByRole("button", { name: "Скачать .xlsx" })).toBeDisabled();
    expect(getMock).not.toHaveBeenCalled();
  });

  it("switches to PDF and names the file accordingly", async () => {
    const user = userEvent.setup();
    renderModal();

    await user.click(screen.getByRole("button", { name: /PDF/ }));
    await user.click(screen.getByRole("button", { name: "Скачать .pdf" }));

    // Параметр называется export: format зарезервирован DRF.
    expect(getMock.mock.calls[0][1].params.export).toBe("pdf");
    expect(downloadMock.mock.calls[0][1]).toMatch(/\.pdf$/);
  });

  it("keeps the section choice when the format changes", async () => {
    const user = userEvent.setup();
    renderModal();

    await user.click(screen.getByRole("button", { name: /Позиции/ }));
    await user.click(screen.getByRole("button", { name: /PDF/ }));
    await user.click(screen.getByRole("button", { name: "Скачать .pdf" }));

    const params = getMock.mock.calls[0][1].params;
    expect(params.export).toBe("pdf");
    expect(params.sections).toBe("summary,clients,ledger,orders,payments,debts");
  });

  it("shows the server reason from a Blob error body instead of a generic error", async () => {
    const error = new AxiosError("failed");
    error.response = {
      status: 400,
      statusText: "",
      data: new Blob([JSON.stringify({ detail: "Период больше года" })], { type: "application/json" }),
      headers: new AxiosHeaders(),
      config: { headers: new AxiosHeaders() },
    };
    getMock.mockRejectedValue(error);
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderModal(onClose);

    await user.click(screen.getByRole("button", { name: "Скачать .xlsx" }));

    expect(await screen.findByText("Период больше года")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(downloadMock).not.toHaveBeenCalled();
  });
});
