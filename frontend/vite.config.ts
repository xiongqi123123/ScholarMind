import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Same origin in development, so the backend's session cookie just works.
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  // ponytail: the app chunk is ~170 kB gzip, fine on the lab network; split routes with lazy()
  // if it grows much further. The largest chunk is PDF.js (~620 kB, loaded only when a PDF is
  // opened), hence the limit.
  build: { chunkSizeWarningLimit: 700 },
})
