import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { Toaster } from "@/components/ui/toaster";
import { THEME_INIT_SCRIPT } from "@/lib/theme";
import "./globals.css";

const inter = Inter({
  subsets: ["latin", "cyrillic"],
  variable: "--font-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "АСЫЛ-LTD — Система учёта",
  description: "Внутренняя CRM мукомольного цеха Асыл-LTD",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // Скрипт темы ставит .dark на <html> до гидратации — расхождение класса ожидаемое.
    <html lang="ru" className={inter.variable} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        {children}
        <Toaster />
      </body>
    </html>
  );
}
