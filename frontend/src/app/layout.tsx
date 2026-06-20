import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "АСЫЛ-LTD — Система учёта",
  description: "Внутренняя CRM мукомольного цеха Асыл-LTD",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
