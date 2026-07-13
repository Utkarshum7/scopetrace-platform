import colors from 'tailwindcss/colors'

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
        },
        // Phase 8a.1 -- semantic aliases for colors ALREADY in use throughout
        // the app (StatusBadge, ErrorState, Trend, LoginPage's error banner
        // all independently converged on emerald=good/rose=bad/amber=caution
        // well before this token existed). Each alias is the exact, full
        // Tailwind shade scale for that color (imported from
        // 'tailwindcss/colors', the same values `bg-emerald-950`/
        // `text-emerald-400`/etc. already resolve to) -- so `bg-success-950`
        // and `bg-emerald-950` render identically. This is intentionally a
        // pure alias, not a new palette: existing components keep using
        // their raw `emerald-`/`rose-`/`amber-`/`sky-` classes unchanged
        // (migrating them is a separate, later adoption pass, not a
        // visual-identity change), and new code (starting with the toast
        // system, 8a.5) gets a semantic vocabulary instead of having to
        // independently rediscover "which raw color means success here".
        success: colors.emerald,
        warning: colors.amber,
        danger: colors.rose,
        info: colors.sky,
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
      },
      // Phase 8a.1 -- typography scale. Tailwind's default scale jumps
      // text-xs (12px) -> text-sm (14px) with nothing smaller, but this
      // app's dense uppercase micro-labels (StatusBadge, WidgetFrame
      // subtitles, table headers, KPI captions) settled on 9-11px sizes,
      // expressed as ~113 one-off `text-[10px]`/`text-[11px]`/`text-[9px]`
      // arbitrary values with no shared name. These three new steps use
      // the EXACT pixel values already in use -- defining the scale here
      // does not, by itself, change how anything currently renders;
      // migrating existing arbitrary-value usages onto these names is
      // separate adoption work (8a.3), not a redesign.
      fontSize: {
        '3xs': ['9px', { lineHeight: '12px' }],
        '2xs': ['10px', { lineHeight: '14px' }],
        'xxs': ['11px', { lineHeight: '15px' }],
      },
      // Phase 8a.1 -- named structural dimensions. Tailwind's default
      // spacing scale (space-1..96, all 4px-based) already covers this
      // app's padding/gap usage consistently (p-5/p-6, gap-4/gap-6/gap-8
      // throughout) -- that scale needed no changes. The gap was two fixed
      // LAYOUT dimensions (sidebar width, detail-drawer width) expressed as
      // unnamed arbitrary values (`w-[260px]`, `w-[380px]`) instead of a
      // documented token. Named here with their exact current values.
      width: {
        sidebar: '260px',
        drawer: '380px',
      },
      // Phase 8a.1 -- fixes 3 currently-BROKEN animation classes. Tailwind
      // silently drops an unrecognized `animate-*` utility (no error, no
      // warning), so `animate-fadeIn` (DashboardPage/ESGAssistantPage/
      // RecordsPage/UploadPage), `animate-slideIn` (RecordsPage's detail
      // drawer), and `animate-shake` (LoginPage's error banner) have been
      // silent no-ops since they were written -- referenced in 6 places,
      // defined nowhere. Adding the matching keyframes/animation entries
      // here is the ENTIRE fix: Tailwind auto-generates `animate-{name}`
      // utilities from these keys, so the 6 existing call sites start
      // working with zero changes to those files.
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideIn: {
          '0%': { opacity: '0', transform: 'translateX(16px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        shake: {
          '0%, 100%': { transform: 'translateX(0)' },
          '20%, 60%': { transform: 'translateX(-6px)' },
          '40%, 80%': { transform: 'translateX(6px)' },
        },
      },
      animation: {
        fadeIn: 'fadeIn 250ms ease-out',
        slideIn: 'slideIn 280ms ease-out',
        shake: 'shake 400ms ease-in-out',
      },
    },
  },
  plugins: [],
}

