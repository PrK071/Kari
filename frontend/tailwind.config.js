/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        app: "#080c0e",
        panel: "#101518",
        soft: "#151c20",
        line: "#273238",
        muted: "#8c999f",
        accent: "#62d69a",
        "accent-dim": "#b7f1d1",
      },
      boxShadow: {
        glow: "0 14px 36px -20px rgba(98,214,154,0.34)",
        card: "0 12px 26px -22px rgba(0,0,0,0.92)",
      },
      backgroundImage: {
        "fade-app":
          "linear-gradient(to top, #000000 8%, rgba(0,0,0,0.72) 46%, rgba(0,0,0,0) 100%)",
      },
    },
  },
  plugins: [],
}
