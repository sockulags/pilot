import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export (out/) so the FastAPI backend can serve the UI from one origin.
  // The app is fully client-side (no server components / API routes), so export works.
  output: "export",
  images: { unoptimized: true },
  allowedDevOrigins: ["192.168.50.9"],
};

export default nextConfig;
