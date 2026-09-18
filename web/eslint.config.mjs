import nextCoreWebVitals from "eslint-config-next/core-web-vitals";

// eslint-config-next ships a flat config array (no @eslint/eslintrc bridge
// needed); ESLint 10 dropped the legacy eslintrc path that `FlatCompat` used.
const config = [
  ...nextCoreWebVitals,
  {
    ignores: ["node_modules/**", ".next/**", "out/**", "src/data/**", "public/data/**"],
  },
];

export default config;
