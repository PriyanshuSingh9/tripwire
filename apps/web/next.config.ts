import type { NextConfig } from "next";

/**
 * The browser calls the same-origin `/api` path. Next.js rewrites it to the
 * FastAPI service so local development needs no CORS configuration on the API
 * and no credentials in the client bundle. The origin is read server-side from
 * TRACEAML_API_ORIGIN, matching the value used by the API itself.
 */
const apiOrigin = process.env.TRACEAML_API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
