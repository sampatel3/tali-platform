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
        'taali-purple': 'var(--taali-purple)',
        'taali-purple-dark': 'var(--taali-purple-hover)',
        'taali-success': 'var(--taali-success)',
        'taali-warning': 'var(--taali-warning)',
        'taali-danger': 'var(--taali-danger)',
        'taali-info': 'var(--taali-info)',
      },
      fontFamily: {
        sans: ['var(--taali-font)'],
        mono: ['var(--taali-font-mono)'],
      },
      borderRadius: {
        none: '0px',
        sm: '0px',
        DEFAULT: '0px',
        md: '0px',
        lg: '0px',
        xl: '0px',
        '2xl': '0px',
        '3xl': '0px',
        full: '0px',
      },
    },
  },
  plugins: [],
}
