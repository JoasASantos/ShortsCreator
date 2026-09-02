/** @type {import('next').NextConfig} */
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};
