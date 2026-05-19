import type { Config } from 'tailwindcss'
import typography from '@tailwindcss/typography'

/**
 * Project-wide theme.
 *
 * Convention: prefer the semantic names below over raw Tailwind utilities
 * (text-gray-500, bg-blue-600, etc.) inside the app. Raw utilities are fine
 * for one-offs; once a pattern repeats 3+ places, promote it here.
 *
 * - primary       — interactive accent (links, primary buttons)
 * - ink           — body text + variants for de-emphasized content
 * - surface       — page/card backgrounds
 * - border        — dividers and outlines
 * - danger / warn — destructive actions and cautions
 *
 * Adding a new token: pick a NAME (not a value), document its intent in this
 * comment, then add to the colors map. UI code references the name.
 */
const config: Config = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: '#2563eb', // blue-600
          hover: '#1d4ed8',
          muted: '#dbeafe',
        },
        ink: {
          DEFAULT: '#111827', // gray-900
          muted: '#6b7280',   // gray-500
          faint: '#9ca3af',   // gray-400
          inverse: '#ffffff',
        },
        surface: {
          DEFAULT: '#ffffff',
          muted: '#f9fafb',   // gray-50
          raised: '#ffffff',
          subtle: '#f3f4f6',  // gray-100
        },
        border: {
          DEFAULT: '#e5e7eb', // gray-200
          strong: '#d1d5db',  // gray-300
          focus: '#2563eb',
        },
        danger: {
          DEFAULT: '#dc2626',
          hover: '#b91c1c',
          muted: '#fee2e2',
        },
        warn: {
          DEFAULT: '#d97706',
          muted: '#fef3c7',
        },
      },
    },
  },
  plugins: [typography],
}
export default config
