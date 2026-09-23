import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { PlateInput, type PlateInputProps } from "./plate-input";

function Field({
  initial = "",
  onValue,
  ...props
}: Partial<PlateInputProps> & { initial?: string; onValue?: (value: string) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <PlateInput
      aria-label="Тягач"
      value={value}
      onChange={(next) => {
        setValue(next);
        onValue?.(next);
      }}
      {...props}
    />
  );
}

describe("PlateInput", () => {
  it("хранит номер слитно, а показывает группами", async () => {
    const user = userEvent.setup();
    const onValue = vi.fn();
    render(<Field onValue={onValue} />);

    await user.type(screen.getByLabelText("Тягач"), "403 bjn 13");

    expect(onValue).toHaveBeenLastCalledWith("403BJN13");
    expect(screen.getByLabelText("Тягач")).toHaveValue("403 BJN 13");
    expect(screen.getByLabelText("Страна тягача")).toHaveValue("KZ");
  });

  it("не портит киргизский номер и сам выбирает страну", async () => {
    const user = userEvent.setup();
    const onValue = vi.fn();
    render(<Field onValue={onValue} />);

    await user.type(screen.getByLabelText("Тягач"), "07kg695adt");

    expect(onValue).toHaveBeenLastCalledWith("07KG695ADT");
    expect(screen.getByLabelText("Тягач")).toHaveValue("07 KG 695 ADT");
    expect(screen.getByLabelText("Страна тягача")).toHaveValue("KG");
  });

  it("переводит кириллицу-двойники в латиницу на вводе", async () => {
    const user = userEvent.setup();
    const onValue = vi.fn();
    render(<Field onValue={onValue} />);

    await user.type(screen.getByLabelText("Тягач"), "а123вс77");

    expect(onValue).toHaveBeenLastCalledWith("A123BC77");
    expect(screen.getByLabelText("Страна тягача")).toHaveValue("RU");
  });

  it("страна, угаданная по началу номера, не залипает: неоднозначный номер — страны клиента", async () => {
    const user = userEvent.setup();
    const onValue = vi.fn();
    render(<Field defaultCountry="Узбекистан" onValue={onValue} />);
    const field = screen.getByLabelText("Тягач");

    // «01123AB» — уже полный киргизский номер без «KG»…
    await user.type(field, "01123ab");
    expect(screen.getByLabelText("Страна тягача")).toHaveValue("KG");
    // …а «01123ABC» — и узбекский номер юрлица: остаётся страна клиента.
    await user.type(field, "c");

    expect(onValue).toHaveBeenLastCalledWith("01123ABC");
    expect(screen.getByLabelText("Страна тягача")).toHaveValue("UZ");
  });

  it("выбранную вручную страну номер не перебивает, пока под неё подходит", async () => {
    const user = userEvent.setup();
    render(<Field />);

    await user.selectOptions(screen.getByLabelText("Страна тягача"), "UZ");
    await user.type(screen.getByLabelText("Тягач"), "01123");

    expect(screen.getByLabelText("Страна тягача")).toHaveValue("UZ");
    expect(screen.getByLabelText("Тягач")).toHaveValue("01 123");
  });

  it("пустое поле подсказывает маску страны клиента", () => {
    render(<Field defaultCountry="Кыргызстан" />);

    expect(screen.getByLabelText("Страна тягача")).toHaveValue("KG");
    expect(screen.getByText("00 KG 000 AAA")).toBeInTheDocument();
  });

  it("у «Другой» страны маски нет", async () => {
    const user = userEvent.setup();
    render(<Field />);

    await user.selectOptions(screen.getByLabelText("Страна тягача"), "");

    expect(screen.getByLabelText("Страна тягача")).toHaveValue("");
    expect(screen.queryByText("000 AAA 00")).not.toBeInTheDocument();
  });

  it("прицеп — со своей подписью страны и маской", () => {
    render(<Field aria-label="Прицеп" kind="trailer" />);

    expect(screen.getByLabelText("Страна прицепа")).toHaveValue("KZ");
    expect(screen.getByText("000 AA 00")).toBeInTheDocument();
  });

  it("Backspace после пробела стирает знак, а не упирается в пробел", async () => {
    const user = userEvent.setup();
    const onValue = vi.fn();
    render(<Field initial="403B" onValue={onValue} />);
    const field = screen.getByLabelText("Тягач") as HTMLInputElement;

    await user.click(field);
    field.setSelectionRange(4, 4);
    await user.keyboard("{Backspace}");

    expect(onValue).toHaveBeenLastCalledWith("40B");
  });

  it("предупреждает о незнакомом формате, когда поле покинули", async () => {
    const user = userEvent.setup();
    render(
      <>
        <Field warning />
        <button type="button">Дальше</button>
      </>,
    );

    await user.type(screen.getByLabelText("Тягач"), "client777");
    expect(screen.queryByText(/не похож/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Дальше" }));

    expect(screen.getByText(/не похож на номера KZ, KG, UZ, RU/)).toBeInTheDocument();
  });

  it("номер с ошибкой не предупреждает ещё и о формате: причину пишет форма", async () => {
    const user = userEvent.setup();
    render(
      <>
        <Field warning aria-invalid />
        <button type="button">Дальше</button>
      </>,
    );

    await user.type(screen.getByLabelText("Тягач"), "12");
    await user.click(screen.getByRole("button", { name: "Дальше" }));

    expect(screen.queryByText(/не похож/)).not.toBeInTheDocument();
  });
});
