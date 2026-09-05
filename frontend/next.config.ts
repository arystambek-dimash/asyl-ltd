import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const securityHeaders = [
  {
    key: "Content-Security-Policy",
    value: "base-uri 'self'; frame-ancestors 'none'; object-src 'none'",
  },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: process.cwd(),
  poweredByHeader: false,
  images: {
    // Bound the self-hosted image cache so attacker-controlled variants cannot
    // fill the server disk. ApiPay is the only approved remote image source.
    maximumDiskCacheSize: 50 * 1024 * 1024,
    remotePatterns: [
      {
        protocol: "https",
        hostname: "api.apipay.kz",
        pathname: "/qr/**",
      },
    ],
  },
  async redirects() {
    // Устаревшие рабочие экраны сохраняют старые закладки без клиентского
    // промежуточного рендера.
    return [
      {
        source: "/management/roles",
        destination: "/management/employees",
        permanent: false,
      },
      {
        source: "/train",
        destination: "/monoblock",
        permanent: false,
      },
      {
        source: "/shipping",
        destination: "/monoblock",
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
