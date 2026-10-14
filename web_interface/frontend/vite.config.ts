import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Vite 5's 'modules' default. Vite 8's newer default (Safari 16.4) emits
  // range media queries, which drop every Tailwind breakpoint on older iOS.
  build: { target: ['es2020', 'edge88', 'firefox78', 'chrome87', 'safari14'] },
  server: {
    port: 3001,
    proxy: {
      // In compose the backend is another container, so localhost is wrong.
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: (process.env.VITE_API_TARGET || 'http://localhost:8000').replace(/^http/, 'ws'),
        ws: true,
      },
    },
  },
})
