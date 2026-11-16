import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const devBackend = env.VITE_DEV_BACKEND || process.env.VITE_DEV_BACKEND || 'http://localhost:8000'
  return {
    plugins: [react()],
    server: {
      port: 5173,
      // Dev: gọi /api/... qua proxy sang backend để khỏi cấu hình CORS.
      // Đổi đích bằng biến môi trường VITE_DEV_BACKEND (mặc định backend Python local).
      proxy: {
        '/api': {
          target: devBackend,
          changeOrigin: true,
        },
      },
    },
  }
})
