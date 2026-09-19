import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  logLevel: 'info',
  server: {
    proxy: {
      '/api/v1/auth/wr1/': {
        target: process.env.WR_CORE_API_PROXY || 'http://127.0.0.1:8000',
      },
    },
  },
  optimizeDeps: {
    include: ['gsap'],
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'), // Set the '@' alias to point to the src directory
    },
  },
})
