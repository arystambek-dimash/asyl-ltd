import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PassageNumberEditor } from "./passage-number-editor";
import type { GrainWagon } from "@/lib/types";

const patchMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { patch: patchMock },
  apiError: () => "Машина 465BDS13 уже находится на территории",
}));

function wagon(overrides: Partial<GrainWagon> = {}): GrainWagon {
  return { id: 7, number: "", direction: "passage", status: "at_silo", ...overrides } as GrainWagon;
}

describe("PassageNumberEditor", () => {
  beforeEach(() => patchMock.mockClear());

  it("flags a passage without a plate and lets an operator type it in uppercase", async () => {
    patchMock.mockResolvedValue({ data: {} });
    const onChanged = vi.fn();
    render(<PassageNumberEditor wagon={wagon()} canEdit onChanged={onChanged} />);

    expect(screen.getByText("номер не распознан")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Указать номер/ }));
    await userEvent.type(screen.getByLabelText("Номер машины"), "465bds13");
    await userEvent.click(screen.getByRole("button", { name: /Сохранить/ }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(patchMock).toHaveBeenCalledWith("/grain/wagons/7/number/", { number: "465BDS13" });
  });

  it("shows the backend rejection next to the field", async () => {
    patchMock.mockRejectedValueOnce({ response: { data: { code: "passage_already_on_site" } } });
    render(<PassageNumberEditor wagon={wagon({ number: "111AAA01" })} canEdit onChanged={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /Изменить номер/ }));
    await userEvent.clear(screen.getByLabelText("Номер машины"));
    await userEvent.type(screen.getByLabelText("Номер машины"), "465BDS13");
    await userEvent.click(screen.getByRole("button", { name: /Сохранить/ }));

    await waitFor(() => expect(patchMock).toHaveBeenCalledWith("/grain/wagons/7/number/", { number: "465BDS13" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("уже находится на территории");
  });

  it("renders only the badge for viewers and nothing for intake wagons or finished trips", () => {
    const { container, rerender } = render(<PassageNumberEditor wagon={wagon()} canEdit={false} onChanged={vi.fn()} />);
    expect(screen.getByText("номер не распознан")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();

    rerender(<PassageNumberEditor wagon={wagon({ direction: "intake" })} canEdit onChanged={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();

    rerender(
      <PassageNumberEditor wagon={wagon({ number: "465BDS13", status: "completed" })} canEdit onChanged={vi.fn()} />,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
