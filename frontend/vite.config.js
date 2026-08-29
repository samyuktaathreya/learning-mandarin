import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const isProd = env.VITE_ENVIRONMENT === 'PROD' || mode === 'production'

  return {
    plugins: [react()],
    // Replace YOUR-REPO-NAME with your actual repository name:
    base: isProd ? '/' : '/',
    server: {
      host: '0.0.0.0',
      port: 5173,
      proxy: {
        '/api': 'http://127.0.0.1:8000',
      },
    },
  }
})