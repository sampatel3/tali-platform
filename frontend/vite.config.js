import { defineConfig } from 'vite'
import { fileURLToPath, URL } from 'node:url'
import react from '@vitejs/plugin-react'
import staticDeckTokenPlugin from './scripts/vite-static-deck-token-plugin.mjs'

const vendor = (p) => fileURLToPath(new URL(`./vendor/mainspring/${p}`, import.meta.url))
const inPackage = (id, packageName) => id.replaceAll('\\', '/').includes(`/node_modules/${packageName}/`)
const packageGroup = (name, packages, priority) => ({
  name,
  test: (id) => packages.some((pkg) => inPackage(id, pkg)),
  priority,
  // Rolldown's compatibility implementation of manualChunks recursively
  // absorbs dependencies by default. That pulled React into charts_vendor,
  // making every route download the otherwise lazy Recharts bundle. Keep
  // each group explicit so lazy feature dependencies stay behind their real
  // import boundary while shared dependencies retain their own chunk.
  includeDependenciesRecursively: false,
})

const codeSplitting = {
  groups: [
    packageGroup(
      'react_vendor',
      ['react', 'react-dom', 'react-router', 'react-router-dom', 'scheduler'],
      50,
    ),
    packageGroup('charts_vendor', ['recharts'], 40),
    packageGroup('monaco_vendor', ['@monaco-editor/react', 'monaco-editor'], 40),
    packageGroup('icons_vendor', ['lucide-react'], 40),
    packageGroup('graph_vendor', ['cytoscape'], 40),
  ],
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
    rolldownOptions: {
      input: {
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        developers: fileURLToPath(new URL('./developers.html', import.meta.url)),
        blog: fileURLToPath(new URL('./blog.html', import.meta.url)),
        blogAiNative: fileURLToPath(new URL('./blog-ai-native.html', import.meta.url)),
        terms: fileURLToPath(new URL('./terms.html', import.meta.url)),
        privacy: fileURLToPath(new URL('./privacy.html', import.meta.url)),
      },
      output: {
        codeSplitting,
        // `strictExecutionOrder` currently leaves the deferred `domMax`
        // re-export uninitialised in Vite 8/Rolldown. The production-artifact
        // gate imports that chunk directly, so a future bundler change cannot
        // silently return the blank LazyMotion UI this option caused.
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
