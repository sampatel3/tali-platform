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
        'taali-purple': '#9D00FF',
        'taali-purple-dark': '#7B00CC',
      }
    },
  },
  plugins: [],
}
