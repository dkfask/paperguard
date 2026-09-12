/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // 映射 Stitch design tokens 到 Tailwind
        primary: 'var(--primary)',
        'primary-hover': 'var(--primary-hover)',
        'primary-container': 'var(--primary-container)',
        secondary: 'var(--secondary)',
        'secondary-container': 'var(--secondary-container)',
        tertiary: 'var(--tertiary)',
        'tertiary-container': 'var(--tertiary-container)',
        error: 'var(--error)',
        'error-container': 'var(--error-container)',

        surface: 'var(--surface)',
        'surface-bright': 'var(--surface-bright)',
        'surface-dim': 'var(--surface-dim)',
        'surface-variant': 'var(--surface-variant)',
        'surface-container': 'var(--surface-container)',
        'surface-container-low': 'var(--surface-container-low)',
        'surface-container-high': 'var(--surface-container-high)',
        'surface-container-lowest': 'var(--surface-container-lowest)',
        'surface-container-highest': 'var(--surface-container-highest)',

        'on-surface': 'var(--on-surface)',
        'on-surface-variant': 'var(--on-surface-variant)',
        'on-primary': 'var(--on-primary)',
        'on-secondary': 'var(--on-secondary)',
        'on-tertiary': 'var(--on-tertiary)',
        'on-error': 'var(--on-error)',

        border: 'var(--border)',
        outline: 'var(--outline)',
        'outline-variant': 'var(--outline-variant)',
      },
      borderRadius: {
        'xs': 'var(--radius-xs)',
        'sm': 'var(--radius-sm)',
        'md': 'var(--radius-md)',
        'lg': 'var(--radius-lg)',
        'xl': 'var(--radius-xl)',
      },
      fontFamily: {
        sans: ['Geist', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
        serif: ['Source Serif 4', 'serif'],
      },
      boxShadow: {
        'soft': 'var(--shadow-soft)',
        'card': 'var(--shadow-card)',
        'nav': 'var(--shadow-nav)',
      },
    },
  },
  plugins: [],
}
