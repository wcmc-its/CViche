import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
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
