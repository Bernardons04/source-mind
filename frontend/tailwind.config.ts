import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        panel: "var(--panel)",
        "panel-2": "var(--panel-2)",
        "panel-hover": "var(--panel-hover)",
        border: "var(--border)",
        text: "var(--text)",
        muted: "var(--muted)",
        soft: "var(--soft)",
        accent: "var(--accent)",
        "accent-ink": "var(--accent-ink)",
      },
      borderRadius: {
        custom: "var(--radius)",
      },
    },
  },
  plugins: [],
};
export default config;
