import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

// Остальные security-заголовки (HSTS, nosniff, Referrer, запрет фреймов)
// ставит nginx: deploy/nginx/conf.d/snippets/security-headers.conf.
const securityHeaders = [
  {
    key: "Content-Security-Policy",
    value: "base-uri 'self'; frame-ancestors 'none'; object-src 'none'",
  },
];

// Dev как прод: API на том же origin (в проде /api/ отдаёт nginx), иначе браузер
// не сохранит и не пошлёт HttpOnly refresh-cookie (SameSite=Strict). Запуск:
// NEXT_PUBLIC_API_URL=/api npm run dev, бэкенд — DEV_API_ORIGIN (по умолчанию 127.0.0.1:8000,
// прод — https://asyl-ltd.kz: прокси ставит Host цели и передаёт Origin страницы).
const isDev = process.env.NODE_ENV === "development";
const devApiOrigin = process.env.DEV_API_ORIGIN || "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: process.cwd(),
  poweredByHeader: false,
  images: {
    // Bound the self-hosted image cache so attacker-controlled variants cannot
    // fill the server disk. Remote images are rendered with `unoptimized`.
    maximumDiskCacheSize: 50 * 1024 * 1024,
  },
  // URL Django заканчиваются на «/»: без этого dev-сервер отвечал бы 308 на /api/x/ → /api/x до прокси.
  skipTrailingSlashRedirect: isDev,
  async rewrites() {
    if (!isDev) return [];
    return [
      { source: "/api/:path*/", destination: `${devApiOrigin}/api/:path*/` },
      { source: "/api/:path*", destination: `${devApiOrigin}/api/:path*` },
    ];
  },
  async redirects() {
    // Старые адреса из закладок: все редиректы здесь, без страниц-заглушек
    // с redirect().
    return [
      {
        source: "/shipping",
        destination: "/monoblock",
        permanent: false,
      },
      {
        // Иначе адрес поймает portal/orders/[id] с id="new".
        source: "/portal/orders/new",
        destination: "/portal/cart",
        permanent: false,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },
};

// Sentry фронта только браузерный (src/instrumentation-client.ts): у контейнера
// нет выхода в сеть, поэтому серверного instrumentation.ts нет намеренно.
process.env.SENTRY_SUPPRESS_INSTRUMENTATION_FILE_WARNING ??= "1";

const canUploadSourceMaps = Boolean(
  process.env.SENTRY_AUTH_TOKEN && process.env.SENTRY_ORG && process.env.SENTRY_PROJECT,
);

export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,
  telemetry: false,
  silent: !canUploadSourceMaps,
  release: {
    name: process.env.NEXT_PUBLIC_APP_RELEASE || "development",
    create: canUploadSourceMaps,
    finalize: canUploadSourceMaps,
  },
  sourcemaps: {
    disable: !canUploadSourceMaps,
    deleteSourcemapsAfterUpload: true,
  },
  widenClientFileUpload: canUploadSourceMaps,
  webpack: {
    treeshake: { removeDebugLogging: true },
  },
});
