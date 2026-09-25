import { makeDepartment } from "@/test-utils/factories";

/** Ответ `/departments/` для страницы кассы: два активных отдела. */
export const CASHIER_DEPARTMENTS = [
  makeDepartment({ id: 1, code: "main", name: "Мельница", color: "#123456" }),
  makeDepartment({ id: 2, code: "field", name: "Нью-Сити", color: "#654321", is_default: false }),
];

/** matchMedia всегда совпадает: useIsMobile() → true, страница рисует телефонный вид. */
export function stubPhoneMatchMedia() {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: () => ({ matches: true, media: "", addEventListener: () => {}, removeEventListener: () => {} }),
  });
}
