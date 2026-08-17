"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="ru">
      <body>
        <main className="flex min-h-screen flex-col items-center justify-center gap-3 px-4 text-center">
          <div className="text-sm font-medium">Не удалось открыть систему</div>
          <div className="max-w-sm text-xs text-gray-600">
            Произошла непредвиденная ошибка. Попробуйте ещё раз; если ошибка повторится, сообщите администратору.
          </div>
          <button
            type="button"
            onClick={reset}
            className="mt-1 rounded-md border px-3 py-1.5 text-sm font-medium shadow-sm"
          >
            Повторить
          </button>
        </main>
      </body>
    </html>
  );
}
