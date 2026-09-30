/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Warm page chrome from the 2026 redesign (#1112). Ordered light to dark.
        sand: {
          50: '#FAF6EE',   // table header, sunken panel
          100: '#F3EAD7',  // page background
          150: '#EFE2C8',  // top bar
          200: '#EDE3CF',  // rule inside a card
          300: '#E6DAC1',  // card border
          350: '#E2D3B4',  // top bar rule
          400: '#DCCFB3',  // input / chip border
        },
        ink: '#1F2328',    // active tab rule, dark chip, avatar
        primary: {
          50: '#eff6ff',
          100: '#dbeafe',
          200: '#bfdbfe',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
        },
        surface: {
          DEFAULT: '#ffffff',
          muted: '#f9fafb',
          raised: '#f3f4f6',
        },
        success: {
          50: '#f0fdf4',
          100: '#dcfce7',
          600: '#16a34a',
          700: '#15803d',
          800: '#166534',
        },
        error: {
          50: '#fef2f2',
          100: '#fee2e2',
          600: '#dc2626',
          700: '#b91c1c',
          800: '#991b1b',
        },
        warning: {
          50: '#fffbeb',
          100: '#fef3c7',
          800: '#92400e',
        },
      },
      zIndex: {
        modal: '50',
        overlay: '40',
        dropdown: '30',
        sticky: '20',
        header: '10',
      },
    },
  },
  plugins: [],
}
