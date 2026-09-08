import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "media",
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        accent: {
          DEFAULT: "#4f46e5",
          light: "#6366f1",
          dark: "#4338ca",
        },
      },
    },
  },
  plugins: [],
};

export default config;
