import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': { target: process.env.VERIFORGE_API_PROXY ?? 'http://127.0.0.1:18000', changeOrigin: false } } },
  test: { environment: 'jsdom', setupFiles: ['./src/test-setup.ts'], clearMocks: true, testTimeout: 15000 },
})
