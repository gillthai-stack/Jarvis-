import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Relative asset paths let the same build run from `npm run preview` or from the Jarvis server at /hud/.
  base: './',
  server: {
    port: 5173,
    open: true,
    // Forward API and WebSocket calls to the Jarvis Python server so the browser never needs the API key.
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/ws': { target: 'ws://127.0.0.1:8765', ws: true },
    },
  },
  // three.js plus React Three Fiber is a large bundle on its own; that's expected for a 3D app.
  build: { chunkSizeWarningLimit: 1600 },
})
