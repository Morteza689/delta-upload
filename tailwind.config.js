/** @type {import('tailwindcss').Config} */
module.exports = {
  // Everywhere a Tailwind class can appear: the templates, and the widget
  // classes declared on the Python forms.
  content: [
    './templates/**/*.html',
    './*/templates/**/*.html',
    './*/forms.py',
  ],
  darkMode: 'media',
  theme: {
    extend: {
      fontFamily: {
        // Vazirmatn for Persian, with the system stack behind it for Latin.
        sans: ['Vazirmatn', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
