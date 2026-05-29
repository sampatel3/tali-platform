/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class', '[data-theme="dark"]'],
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
    // Vendored @mainspring/ui primitives carry their class names as literals;
    // scan them so the semantic-colour bridge below actually emits CSS when a
    // primitive is imported into a Taali surface.
    "./vendor/mainspring/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        taali: {
          ink: 'var(--ink)',
          'ink-2': 'var(--ink-2)',
          'ink-soft': 'var(--ink-soft)',
          mute: 'var(--mute)',
          'mute-2': 'var(--mute-2)',
          line: 'var(--line)',
          'line-2': 'var(--line-2)',
          bg: 'var(--bg)',
          'bg-2': 'var(--bg-2)',
          'bg-3': 'var(--bg-3)',
          purple: 'var(--purple)',
          'purple-2': 'var(--purple-2)',
          'purple-soft': 'var(--purple-soft)',
          lime: 'var(--lime)',
          peach: 'var(--peach)',
          green: 'var(--green)',
          red: 'var(--red)',
          amber: 'var(--amber)',
          workable: 'var(--workable)',
        },
        workable: {
          DEFAULT: 'var(--workable)',
          dark: 'var(--workable-dark)',
        },
        'taali-purple': 'var(--purple)',
        'taali-purple-dark': 'var(--purple-2)',
        'taali-success': 'var(--green)',
        'taali-warning': 'var(--amber)',
        'taali-danger': 'var(--red)',
        'taali-info': 'var(--purple)',
        // Bridge: @mainspring/ui primitives (CommandBar, Toast, …) are written
        // against mainspring's semantic colour names. Map each by *role* onto
        // Taali's tokens so a vendored primitive re-skins to the Taali palette
        // with no edit to the vendored copy. Purely additive — no Taali surface
        // references these names (they all use the `taali-*` namespace).
        panel: 'var(--bg-2)',
        'panel-2': 'var(--bg-3)',
        cloud: 'var(--ink)',
        mute: 'var(--mute)',
        'line-d': 'var(--line)',
        accent: 'var(--purple)',
        warn: 'var(--amber)',
        danger: 'var(--red)',
      },
      fontFamily: {
        display: ['var(--font-display)'],
        sans: ['var(--font-sans)'],
        mono: ['var(--font-mono)'],
      },
      borderRadius: {
        'taali-sm': 'var(--radius-sm)',
        taali: 'var(--radius)',
        'taali-lg': 'var(--radius-lg)',
        'taali-xl': 'var(--radius-xl)',
      },
      boxShadow: {
        'taali-sm': 'var(--shadow-sm)',
        'taali-md': 'var(--shadow-md)',
        'taali-lg': 'var(--shadow-lg)',
      },
    },
  },
  plugins: [],
}
