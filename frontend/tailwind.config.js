/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Modern premium color palette
        brand: {
          50: '#f0fbf6',
          100: '#dcf7ea',
          200: '#bcefd5',
          300: '#8be2b6',
          400: '#53ce91',
          500: '#2ebb72', // ScopeTrace primary green
          600: '#209c5b',
          700: '#1c7b4a',
          800: '#1a623d',
          900: '#165134',
          950: '#0b2d1d',
        },
        slate: {
          950: '#0b0f19', // Sleek dark mode background
        }
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
      },
    },
  },
  plugins: [],
}

