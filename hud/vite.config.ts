import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, open: true },
  // three.js plus React Three Fiber is a large bundle on its own; that's expected for a 3D app.
  build: { chunkSizeWarningLimit: 1600 },
})
