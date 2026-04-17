import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: 'standalone',
  // Mark these packages as external so Turbopack/webpack doesn't try to bundle
  // them — they'll be resolved by Node.js at runtime instead.
  // `undici` is built into Node.js 18+ but has no node_modules entry, so it
  // MUST be external or the build will fail with "Can't resolve 'undici'".
  serverExternalPackages: ["@copilotkit/runtime", "undici"],


  typescript: {
    ignoreBuildErrors: true,
  },

  productionBrowserSourceMaps: false,

  async rewrites() {
    return [
      {
        source: '/agent-api/:path*',
        destination: `${process.env.AGENT_URL || 'http://localhost:8000'}/:path*`,
      },
    ];
  },
};

export default nextConfig;



