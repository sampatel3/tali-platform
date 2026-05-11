import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import staticDeckTokenPlugin from './scripts/vite-static-deck-token-plugin.mjs'

export default defineConfig({
  plugins: [react(), staticDeckTokenPlugin()],
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
