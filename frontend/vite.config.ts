import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: { preserveSymlinks: true },
  server: {
    port: 5173,
    strictPort: true,
    proxy: { '/health': process.env.VISIONFLOW_BACKEND_TARGET || 'http://127.0.0.1:8000' },
  },
})
