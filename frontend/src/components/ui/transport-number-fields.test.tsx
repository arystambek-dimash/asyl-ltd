import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { EMPTY_TRANSPORT_PAIR, type TransportPair } from "@/lib/plates";
import { TransportNumberFields } from "./transport-number-fields";

function Fields(props: Partial<React.ComponentProps<typeof TransportNumberFields>>) {
  const [value, setValue] = useState<TransportPair>(EMPTY_TRANSPORT_PAIR);
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
