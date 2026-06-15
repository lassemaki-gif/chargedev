/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        night: "#0A0F1E",
        card: "#111827",
        border: "#1F2937",
        volt: "#22C55E",        // electric green
        "volt-dim": "#16A34A",
        ash: "#9CA3AF",
        danger: "#EF4444",
      },
      fontFamily: {
        sans: ["'Inter'", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
