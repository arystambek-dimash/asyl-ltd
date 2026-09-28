import { afterEach, describe, expect, it, vi } from "vitest";
import { copyText, whatsappLink } from "./clipboard";

describe("copyText", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("copies through the Clipboard API when it is there", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });

    await expect(copyText("сб 19.09.26")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("сб 19.09.26");
  });

  it("falls back to a hidden field without the Clipboard API", async () => {
    vi.stubGlobal("navigator", {});
    const exec = vi.fn().mockReturnValue(true);
    document.execCommand = exec;

    await expect(copyText("отчёт")).resolves.toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
    expect(document.querySelector("textarea")).toBeNull();
  });
});

describe("whatsappLink", () => {
  it("ссылка WhatsApp — на номер или с выбором чата", () => {
    expect(whatsappLink("77011234567", "Ст. 1 вагон\nД1с")).toBe(
      "https://wa.me/77011234567?text=%D0%A1%D1%82.%201%20%D0%B2%D0%B0%D0%B3%D0%BE%D0%BD%0A%D0%941%D1%81",
    );
    expect(whatsappLink("", "a&b")).toBe("https://wa.me/?text=a%26b");
  });
});
