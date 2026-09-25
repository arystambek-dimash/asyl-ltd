import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { THEME_INIT_SCRIPT } from "@/lib/theme";
import RootLayout from "./layout";

vi.mock("next/font/google", () => ({ Inter: () => ({ variable: "font-sans" }) }));
vi.mock("@/components/ui/toaster", () => ({ Toaster: () => null }));

it("applies the theme in <head> before hydration, so login and loading are not flashed light", () => {
  const html = renderToStaticMarkup(
    <RootLayout>
      <main>Загрузка…</main>
    </RootLayout>,
  );
  const head = html.slice(html.indexOf("<head>"), html.indexOf("</head>"));
  expect(head).toContain(THEME_INIT_SCRIPT);
});
