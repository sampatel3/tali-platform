import { defineConfig } from 'vite'
import { fileURLToPath, URL } from 'node:url'
import react from '@vitejs/plugin-react'
import staticDeckTokenPlugin from './scripts/vite-static-deck-token-plugin.mjs'

const vendor = (p) => fileURLToPath(new URL(`./vendor/mainspring/${p}`, import.meta.url))
const inPackage = (id, packageName) => id.replaceAll('\\', '/').includes(`/node_modules/${packageName}/`)
const manualChunks = (id) => {
  if (['react', 'react-dom', 'react-router', 'react-router-dom', 'scheduler'].some((pkg) => inPackage(id, pkg))) return 'react_vendor'
  if (inPackage(id, 'recharts')) return 'charts_vendor'
  if (['@monaco-editor/react', 'monaco-editor'].some((pkg) => inPackage(id, pkg))) return 'monaco_vendor'
  if (inPackage(id, 'lucide-react')) return 'icons_vendor'
  if (inPackage(id, 'cytoscape')) return 'graph_vendor'
  return undefined
}

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
      input: {
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        developers: fileURLToPath(new URL('./developers.html', import.meta.url)),
        blog: fileURLToPath(new URL('./blog.html', import.meta.url)),
        blogAiNative: fileURLToPath(new URL('./blog-ai-native.html', import.meta.url)),
        terms: fileURLToPath(new URL('./terms.html', import.meta.url)),
        privacy: fileURLToPath(new URL('./privacy.html', import.meta.url)),
      },
      output: {
        manualChunks,
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
