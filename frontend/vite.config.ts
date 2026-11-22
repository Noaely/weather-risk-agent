import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The UI calls /chat and /health on its own origin; Vite forwards them to the FastAPI backend,
// so the browser never makes a cross-origin request (no CORS setup needed).
// BACKEND_URL lets a second copy run against another backend (e.g. port 8001) without editing this file.
const backend = process.env.BACKEND_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/chat': backend,
      '/health': backend,
    },
  },
})
