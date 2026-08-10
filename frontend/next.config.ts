import type { NextConfig } from "next";

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
        destination: "/shipping",
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

export default nextConfig;
