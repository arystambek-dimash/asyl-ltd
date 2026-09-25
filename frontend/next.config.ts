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

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: process.cwd(),
  poweredByHeader: false,
  images: {
    // Bound the self-hosted image cache so attacker-controlled variants cannot
    // fill the server disk. Remote images are rendered with `unoptimized`.
    maximumDiskCacheSize: 50 * 1024 * 1024,
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
