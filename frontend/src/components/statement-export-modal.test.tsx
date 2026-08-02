import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StatementExportModal } from "@/components/statement-export-modal";
import { monthStartLocalIsoDate, todayLocalIsoDate } from "@/lib/utils";

const getMock = vi.hoisted(() => vi.fn());
const downloadMock = vi.hoisted(() => vi.fn());
const useApiMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { get: getMock },
  apiError: () => "Ошибка выписки",
}));
vi.mock("@/lib/download", () => ({ downloadBlob: downloadMock }));
vi.mock("@/lib/use-api", () => ({ useApi: useApiMock }));

const departments = [
  {
    id: 1,
    code: "north",
    name: "Север",
    color: "#315FD5",
    is_active: true,
    is_default: true,
    order_count: 5,
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: 2,
    code: "south",
    name: "Юг",
    color: "#1F9D6A",
    is_active: true,
    is_default: false,
    order_count: 3,
    created_at: "2026-01-02T00:00:00Z",
  },
];

function renderModal(onClose = vi.fn()) {
  render(
    <StatementExportModal
      open
      onClose={onClose}
      endpoint="/clients/statement/"
      filename="clients-full-statement.xlsx"
      title="Общая выписка"
      description="Описание"
      scopeLabel="Все клиенты"
      sheetsLabel="7 листов."
    />,
  );
}

describe("StatementExportModal", () => {
  beforeEach(() => {
    getMock.mockReset();
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
    getMock.mockResolvedValue({ data: new Blob(["xlsx"]) });
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
    getMock.mockResolvedValue({ data: new Blob(["xlsx"]) });
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
});
