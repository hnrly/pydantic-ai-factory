import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The SQLite driver is a native module, so it is loaded at run time, not bundled.
  serverExternalPackages: ["better-sqlite3"],
};

export default nextConfig;
