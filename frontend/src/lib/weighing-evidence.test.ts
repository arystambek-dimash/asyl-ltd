import { describe, expect, it } from "vitest";
import { archStopReasonLabel } from "./weighing-evidence";

describe("archStopReasonLabel", () => {
  it("explains what the operator must do", () => {
    expect(archStopReasonLabel("silo_required")).toBe("Назначьте силос в рейсе — заезд запишется автоматически");
    expect(archStopReasonLabel("no_exit_weight")).toBe(
      "Перед отъездом не было устойчивого веса — укажите выезд вручную",
    );
    expect(archStopReasonLabel("something_new", "Текст с сервера")).toBe("Текст с сервера");
    expect(archStopReasonLabel("something_new")).toBe("Требуется проверка");
    expect(archStopReasonLabel("")).toBe("");
  });

  it("explains the three stop-journal-only reasons", () => {
    expect(archStopReasonLabel("exit_unseen")).toBe("Отъезд не был виден — укажите выезд вручную");
    expect(archStopReasonLabel("wagon_deleted")).toBe("Рейс удалён — стоянку можно закрыть");
    expect(archStopReasonLabel("import_error")).toBe("Ошибка импорта — проверьте рейс");
    // The server detail still wins for unknown codes, even among these three names.
    expect(archStopReasonLabel("exit_unseen_v2", "Текст с сервера")).toBe("Текст с сервера");
  });

  it("explains the two terminal reasons the automation cannot recover from", () => {
    expect(archStopReasonLabel("not_simple_flow")).toBe("Рейс не в коротком потоке — оформите вручную");
    expect(archStopReasonLabel("wrong_scale_action")).toBe("Весы ждут другое действие — проверьте этап рейса");
  });
});
