/**
 * Скопировать текст в буфер. На планшете без HTTPS-контекста Clipboard API
 * нет — тогда старый способ через скрытое поле. `false` — не вышло.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Браузер отказал (нет фокуса или разрешения) — пробуем старый способ.
  }
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.opacity = "0";
  document.body.appendChild(field);
  field.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    field.remove();
  }
}

/**
 * Скопировать текст, который ещё грузится с сервера. Safari на iPhone пускает
 * в буфер только из самого нажатия, а не после ожидания ответа: поэтому запись
 * начинается сразу — ClipboardItem с обещанием текста. Где ClipboardItem нет
 * или браузер отказал — {@link copyText}, когда текст придёт. Не загрузился
 * сам текст — эта ошибка наверх, а не `false`: её покажет кнопка.
 */
export async function copyTextFrom(text: Promise<string>): Promise<boolean> {
  if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
    const blob = text.then((value) => new Blob([value], { type: "text/plain" }));
    // Браузер мог отказать, не дождавшись текста: ошибку загрузки ловит await ниже, не Sentry.
    blob.catch(() => undefined);
    try {
      await navigator.clipboard.write([new ClipboardItem({ "text/plain": blob })]);
      return true;
    } catch {
      // Отказ браузера или ошибка загрузки — второе всплывёт из await ниже.
    }
  }
  return copyText(await text);
}

/** Ссылка wa.me с текстом: на номер или, без номера, с выбором чата. */
export function whatsappLink(phone: string, text: string): string {
  return `https://wa.me/${phone}?text=${encodeURIComponent(text)}`;
}
