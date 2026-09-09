import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { HistoricalTareDialog } from "./historical-tare-dialog";
import type { GrainUnassignedWeighing } from "@/lib/types";

const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  api: { get, post, defaults: { baseURL: "https://test.invalid/api" } },
  apiError: () => "Не удалось сохранить",
}));

describe("historical tare confirmation", () => {
  it("keeps the dialog open while parent polling pauses and sends only the selected evidence", async () => {
    get.mockResolvedValue({
      data: [
        { id: 11, weight_kg: 4000, created_at: "2026-01-01T09:00:00Z", photo_url: "/api/grain/photos/weighing/11/" },
      ],
    });
    post.mockResolvedValue({ data: {} });
    const changed = vi.fn();
    function Parent() {
      const [busy, setBusy] = useState(false);
      return (
        <HistoricalTareDialog
          item={
            {
              id: 5,
              vehicle_number: "123ABC13",
              weight_kg: 9000,
              stable_weight_at: "2026-01-02T09:00:00Z",
            } as GrainUnassignedWeighing
          }
          disabled={busy}
          onChanged={changed}
          onBusyChange={setBusy}
        />
      );
    }
    render(<Parent />);
    expect(get).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Выезд с сохранённой тарой" }));
    await userEvent.click(screen.getByRole("button", { name: "Найти тару" }));
    await userEvent.click(await screen.findByRole("radio"));
    expect(screen.getByRole("button", { name: "Сохранить и завершить вывоз" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Причина использования сохранённой тары"), "Проверена по фото");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить и завершить вывоз" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(post).toHaveBeenCalledWith("/grain/unassigned-weighings/5/historical-exit/", {
      number: "123ABC13",
      reference_record: 11,
      reason: "Проверена по фото",
    });
  });
});
