import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The UI calls /chat and /health on its own origin; Vite forwards them to the FastAPI backend,
// so the browser never makes a cross-origin request (no CORS setup needed).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/chat': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
})
