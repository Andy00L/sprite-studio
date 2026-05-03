/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  corePlugins: {
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: '#0b0d10',
          panel: '#13171c',
          subtle: '#1a1f26',
        },
        accent: {
          DEFAULT: '#6c8cff',
          muted: '#3a4a7a',
        },
        text: {
          DEFAULT: '#e6edf3',
          muted: '#8b95a3',
          subtle: '#5d6675',
        },
        ok: '#5fbf6c',
        warn: '#e0a93f',
        err: '#e25555',
      },
      fontFamily: {
        sans: ['ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
};
