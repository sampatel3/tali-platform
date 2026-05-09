import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          react_vendor: ['react', 'react-dom', 'react-router-dom'],
          charts_vendor: ['recharts'],
          monaco_vendor: ['@monaco-editor/react'],
          icons_vendor: ['lucide-react'],
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.js',
    globals: true,
    // jsdom 28's XHR dispatcher rejects with `UND_ERR_INVALID_ARG`
    // when undici sees an `onError` shape it doesn't expect. The
    // rejections are unhandled and don't fail any individual test,
    // but they make vitest exit non-zero. We tried filtering at
    // process.emit (vitest's listener registers earlier) and bumping
    // CI Node 20 → 22 (jsdom 28 + Node 22 undici still mismatch);
    // neither cleared it. Ignore unhandled errors here so the suite
    // is honest about whether the tests themselves passed or failed.
    dangerouslyIgnoreUnhandledErrors: true,
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
