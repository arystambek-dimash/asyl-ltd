import { describe, expect, it } from "vitest";
import { AxiosError, AxiosHeaders } from "axios";
import { apiError } from "./api";

function withResponse(status: number, data: unknown): AxiosError {
  const error = new AxiosError("failed");
  error.response = {
    status,
    statusText: "",
    data,
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  };
  return error;
}

describe("apiError", () => {
  it("показывает доменное сообщение бэкенда как есть", () => {
    const error = withResponse(400, { detail: "Доступно к оплате: 1500 KZT", code: "payment_exceeds_remaining" });
    expect(apiError(error)).toBe("Доступно к оплате: 1500 KZT");
  });

  it("склеивает ошибки по полям формы", () => {
    const error = withResponse(400, { detail: { amount: ["Обязательное поле"], reason: ["Слишком коротко"] } });
    expect(apiError(error)).toBe("Обязательное поле; Слишком коротко");
  });

  it("молчит про 403 — его показывает общий перехватчик", () => {
    expect(apiError(withResponse(403, { detail: "Нет прав" }))).toBe("");
  });

  it("отличает обрыв связи от падения сервера", () => {
    // Чинится по-разному: связь — проверить интернет, 500 — звать администратора.
    expect(apiError(new AxiosError("Network Error"))).toContain("Нет связи");
    expect(apiError(withResponse(500, {}))).toContain("Сервер не отвечает");
    expect(apiError(withResponse(502, {}))).toContain("Сервер не отвечает");
  });

  it("объясняет 404 и 401 по-человечески", () => {
    expect(apiError(withResponse(404, {}))).toContain("не найдена");
    expect(apiError(withResponse(401, {}))).toContain("Сессия истекла");
  });

  it("оставляет общий текст для прочих ответов без detail", () => {
    expect(apiError(withResponse(400, {}))).toBe("Произошла ошибка. Попробуйте ещё раз.");
  });
});
