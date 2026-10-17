import nextConfig from "eslint-config-next";
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";

/** Flat ESLint config: Next.js and Core Web Vitals rule sets. */
const config = [
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts"],
  },
  ...nextConfig,
  ...nextCoreWebVitals,
];

export default config;
