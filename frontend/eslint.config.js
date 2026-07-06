import js from '@eslint/js'
import globals from 'globals'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'

export default [
  { ignores: ['dist'] },
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    settings: { react: { version: '18.3' } },
    plugins: {
      react,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...react.configs.recommended.rules,
      ...react.configs['jsx-runtime'].rules,
      ...reactHooks.configs.recommended.rules,
      'react/jsx-no-target-blank': 'off',
      // This is a plain JS (not TypeScript) project and has never actually
      // adopted PropTypes — the `prop-types` package isn't even a
      // dependency. Inherited from the default template's recommended
      // ruleset but never followed; off rather than a permanent backlog of
      // unenforceable errors (Phase 5i CI hygiene).
      'react/prop-types': 'off',
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      // Chart library is an implementation detail — it may only be imported by
      // the project chart wrappers in components/charts/.
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: 'recharts',
              message: 'Import project charts from components/charts instead of recharts directly.',
            },
          ],
        },
      ],
    },
  },
  {
    // The chart wrapper directory is the single allowed home for recharts.
    files: ['src/components/charts/**/*.{js,jsx}'],
    rules: { 'no-restricted-imports': 'off' },
  },
]
