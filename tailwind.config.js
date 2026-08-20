/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef4ff",
          100: "#dde7ff",
          500: "#4865f4",
          600: "#3f46d8",
          700: "#3836ac",
          900: "#25235f"
        }
      },
      boxShadow: {
        panel: "0 10px 30px rgba(31, 41, 55, 0.08)"
      }
    }
  },
  plugins: []
};
