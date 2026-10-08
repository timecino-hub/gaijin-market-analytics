/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async headers() {
    return [{
      source: "/vehicle-images/:path*",
      headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }]
    }];
  }
};

export default nextConfig;
