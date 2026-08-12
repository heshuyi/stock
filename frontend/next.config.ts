import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // /api/* is handled by src/app/api/[...path]/route.ts (JSON 503 when backend down)
};

export default nextConfig;
