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

/** Ссылка wa.me с текстом — как на сервере: на номер или, без номера, с выбором чата. */
export function whatsappLink(phone: string, text: string): string {
  return `https://wa.me/${phone}?text=${encodeURIComponent(text)}`;
}
