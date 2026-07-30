import type { Metadata } from "next";
import { Manrope } from "next/font/google";
import Script from "next/script";
import { Toaster } from "@/components/ui/toaster";
import "./globals.css";

const manrope = Manrope({
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
    <html lang="ru" className={manrope.variable} suppressHydrationWarning>
      <head>
        <Script id="theme-init" strategy="beforeInteractive">
          {`try{const t=localStorage.getItem("asyl_theme");const d=t==="dark"||(t==="system"&&matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.classList.toggle("dark",d)}catch{}`}
        </Script>
      </head>
      <body>
        {children}
        <Toaster />
      </body>
    </html>
  );
}
