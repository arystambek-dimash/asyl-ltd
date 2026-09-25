import { act, render } from "@testing-library/react";
import { Suspense, type ComponentType } from "react";

/**
 * Рендерит страницу динамического маршрута `[id]`. Страница читает params
 * через `use()`: без Suspense React подвешивает рендер и документ остаётся
 * пустым, поэтому ждём, пока params разрешатся.
 */
export async function renderRoutePage(Page: ComponentType<{ params: Promise<{ id: string }> }>, id: string) {
  const params = Promise.resolve({ id });
  await act(async () => {
    render(
      <Suspense>
        <Page params={params} />
      </Suspense>,
    );
    await params;
  });
}
