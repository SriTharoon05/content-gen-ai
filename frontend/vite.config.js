import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev the dashboard talks to the backend through this proxy, so there is no CORS to configure.
// In production set VITE_API_BASE to the deployed backend origin + /api.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})
