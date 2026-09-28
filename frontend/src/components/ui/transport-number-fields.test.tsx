import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { EMPTY_TRANSPORT_PAIR, type TransportPair } from "@/lib/plates";
import { TransportNumberFields } from "./transport-number-fields";

function Fields({
  initial = EMPTY_TRANSPORT_PAIR,
  ...props
}: Partial<React.ComponentProps<typeof TransportNumberFields>> & { initial?: TransportPair }) {
  const [value, setValue] = useState<TransportPair>(initial);
  return <TransportNumberFields id="t" transportType="truck" value={value} onChange={setValue} {...props} />;
}

describe("TransportNumberFields", () => {
  it("у машины — тягач и прицеп, Enter переводит к прицепу", async () => {
    const user = userEvent.setup();
    render(<Fields />);

    await user.type(screen.getByLabelText("Тягач"), "07kg695adt{Enter}");

    expect(screen.getByLabelText("Тягач")).toHaveValue("07 KG 695 ADT");
    expect(screen.getByLabelText("Прицеп (необязательно)")).toHaveFocus();
    expect(screen.queryByLabelText("Номер вагона")).not.toBeInTheDocument();
  });

  it("у газели — один номер машины, прицеп появляется по галочке", async () => {
    const user = userEvent.setup();
    render(<Fields truckKind="gazelle" />);

    expect(screen.getByLabelText("Номер машины")).toBeInTheDocument();
    expect(screen.queryByLabelText("Тягач")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Прицеп")).not.toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "Есть прицеп" }));
    await user.type(screen.getByLabelText("Прицеп"), "07kg837pb");

    expect(screen.getByLabelText("Прицеп")).toHaveValue("07 KG 837 PB");
  });

  it("у газели с прицепом галочка стоит, а снятая стирает номер прицепа", async () => {
    const user = userEvent.setup();
    render(<Fields truckKind="gazelle" initial={{ truck_number: "123ABC02", trailer_number: "07KG837PB" }} />);
    const withTrailer = screen.getByRole("checkbox", { name: "Есть прицеп" });
    expect(withTrailer).toBeChecked();
    expect(screen.getByLabelText("Прицеп")).toHaveValue("07 KG 837 PB");

    await user.click(withTrailer);
    expect(screen.queryByLabelText("Прицеп")).not.toBeInTheDocument();
    await user.click(withTrailer);

    expect(screen.getByLabelText("Прицеп")).toHaveValue("");
    expect(screen.getByLabelText("Номер машины")).toHaveValue("123 ABC 02");
  });

  it("у вагона — одно поле, пробелы отбрасываются, номер не обрезается", async () => {
    const user = userEvent.setup();
    render(<Fields transportType="train" />);

    await user.type(screen.getByLabelText("Номер вагона"), "0012 3456");

    expect(screen.getByLabelText("Номер вагона")).toHaveValue("00123456");
    expect(screen.queryByLabelText("Тягач")).not.toBeInTheDocument();
  });

  it("текст ошибки — под полем, true — только подсветка", () => {
    render(<Fields errors={{ truck: "Неверный номер", trailer: true }} />);

    expect(screen.getByLabelText("Тягач")).toHaveAccessibleDescription("Неверный номер");
    expect(screen.getByLabelText("Прицеп (необязательно)")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Прицеп (необязательно)")).not.toHaveAttribute("aria-describedby");
  });
});
