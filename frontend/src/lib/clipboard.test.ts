import { afterEach, describe, expect, it, vi } from "vitest";
import { copyText, copyTextFrom, whatsappLink } from "./clipboard";

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

/** Запись в буфер, какой её видит браузер: ClipboardItem хранит обещание содержимого. */
class FakeClipboardItem {
  constructor(readonly items: Record<string, Promise<Blob>>) {}
}

describe("copyTextFrom", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("starts the clipboard write in the click itself and fills it when the text arrives (Safari)", async () => {
    let written: FakeClipboardItem | null = null;
    const write = vi.fn(async ([item]: FakeClipboardItem[]) => {
      written = item;
      await item.items["text/plain"];
    });
    vi.stubGlobal("ClipboardItem", FakeClipboardItem);
    vi.stubGlobal("navigator", { clipboard: { write, writeText: vi.fn() } });
    let deliver!: (text: string) => void;

    const copied = copyTextFrom(new Promise<string>((resolve) => (deliver = resolve)));

    // Ответа сервера ещё нет, а запись уже начата — синхронно, в самом нажатии.
    expect(write).toHaveBeenCalledTimes(1);
    deliver("Отгрузка 02.10.26\nkz 909 ERD 13");
    await expect(copied).resolves.toBe(true);
    const blob = await written!.items["text/plain"];
    expect(blob.type).toBe("text/plain");
    expect(await blob.text()).toBe("Отгрузка 02.10.26\nkz 909 ERD 13");
  });

  it("without ClipboardItem copies the text once it is there", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });

    await expect(copyTextFrom(Promise.resolve("отчёт"))).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("отчёт");
  });

  it("falls back to copyText when the browser refuses the rich write", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const write = vi.fn().mockRejectedValue(new DOMException("denied", "NotAllowedError"));
    vi.stubGlobal("ClipboardItem", FakeClipboardItem);
    vi.stubGlobal("navigator", { clipboard: { write, writeText } });

    await expect(copyTextFrom(Promise.resolve("отчёт"))).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("отчёт");
  });

  it("a failed text load is the error, not a clipboard refusal", async () => {
    const writeText = vi.fn();
    const write = vi.fn(async ([item]: FakeClipboardItem[]) => {
      await item.items["text/plain"];
    });
    vi.stubGlobal("ClipboardItem", FakeClipboardItem);
    vi.stubGlobal("navigator", { clipboard: { write, writeText } });
    const failure = new Error("Сервер не отвечает");

    await expect(copyTextFrom(Promise.reject(failure))).rejects.toBe(failure);
    expect(writeText).not.toHaveBeenCalled();
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
