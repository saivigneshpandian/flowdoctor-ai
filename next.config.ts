import type { NextConfig } from "next";

// The browser only ever talks to this app. /api/* is proxied to the backend on the server side,
// which keeps the session cookie first-party. Read at build time, so changing it needs a rebuild.
const backendUrl = (process.env.BACKEND_URL || "http://localhost:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  // Produces a self-contained server for the Docker image.
  output: "standalone",
  // The floating dev badge would show up in the demo recording.
  devIndicators: false,
  experimental: {
    // The default is 30 seconds. Chat runs LLM calls and repairs wait on n8n, so both can take longer.
    proxyTimeout: 120_000,
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/:path*` }];
  },
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    root: process.cwd(),
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
