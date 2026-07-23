import { defineConfig } from 'vite'
import { fileURLToPath, URL } from 'node:url'
import react from '@vitejs/plugin-react'
import staticDeckTokenPlugin from './scripts/vite-static-deck-token-plugin.mjs'

const vendor = (p) => fileURLToPath(new URL(`./vendor/mainspring/${p}`, import.meta.url))

export default defineConfig({
  plugins: [react(), staticDeckTokenPlugin()],
  resolve: {
    alias: {
      // Shared mainspring FE primitives, vendored by
      // scripts/vendor_mainspring_ui.sh. Most specific alias first.
      '@mainspring/ui/styles/components.css': vendor('ui/styles/components.css'),
      '@mainspring/ui': vendor('ui/index.ts'),
      '@mainspring/tokens': vendor('tokens/index.ts'),
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          react_vendor: ['react', 'react-dom', 'react-router-dom'],
          charts_vendor: ['recharts'],
          // Reached only through the lazy CodeEditor import, so the Monaco
          // runtime stays out of the initial app load. Seeded with the editor
          // API rather than the `monaco-editor` package entry: the entry is
          // editor.main, which would drag every language Monaco ships back
          // into the graph regardless of what monacoSetup.js imports.
          monaco_vendor: ['@monaco-editor/react', 'monaco-editor/esm/vs/editor/editor.api'],
          icons_vendor: ['lucide-react'],
          graph_vendor: ['cytoscape'],
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
