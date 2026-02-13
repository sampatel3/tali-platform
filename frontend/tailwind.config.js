/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'tali-purple': '#9D00FF',
        'tali-purple-dark': '#7B00CC',
      }
    },
  },
  plugins: [],
}
