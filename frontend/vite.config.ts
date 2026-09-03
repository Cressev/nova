import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Nova 前端构建：与 dsh 相同的 Vite + React 技术栈。
// 产物输出到仓库 static/（FastAPI 的静态目录），资源前缀 /static/。
export default defineConfig({
  plugins: [react()],
  base: '/static/',
  build: {
    outDir: '../static',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
})
