import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({
  plugins: [vue()],
  optimizeDeps: { include: ['pdfjs-dist'] },
  server: { host: '127.0.0.1', port: 5188, strictPort: true, proxy: { '/api': { target: process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true } } },
  build: { chunkSizeWarningLimit: 1100, rollupOptions: { output: { manualChunks: { 'ui-kit': ['naive-ui'], charts: ['echarts'], graph: ['cytoscape'] } } } }
})
