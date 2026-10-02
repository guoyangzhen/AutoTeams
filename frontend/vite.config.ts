import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// B6: 显式设置 root 为 __dirname 的绝对路径，避免在 git worktree + node_modules junction
// 环境下 vite 调用 fs.realpath 将 root 解析为真实路径后被 rollup 当作相对路径拒绝
export default defineConfig({
  root: __dirname,
  // 统一使用项目根目录的 .env（单文件配置），避免各服务各自维护 .env
  envDir: path.resolve(__dirname, '..'),
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      // Python 后端 API
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // collaboration-service HTTP API (Node.js pi.dev SDK)
      '/collab-api': {
        target: 'http://127.0.0.1:3001',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/collab-api/, '/api'),
      },
      // collaboration-service WebSocket
      '/collab-ws': {
        target: 'ws://127.0.0.1:3001',
        changeOrigin: true,
        ws: true,
        rewrite: (p) => p.replace(/^\/collab-ws/, '/ws'),
      },
    },
  },
  build: {
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        // P1-04-A + P2-21: manualChunks 分离 vendor 代码，降低首屏体积
        // chart/flow 为体积最大的两组依赖，必须显式分组：
        // 否则 rollup 会按引用点自动切分，导致 recharts 被复制进多个页面 chunk
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          'ui-vendor': ['framer-motion', 'lucide-react', 'sonner'],
          'markdown-vendor': ['react-markdown', 'remark-gfm', 'rehype-highlight', 'highlight.js'],
          'chart-vendor': ['recharts'],
          'flow-vendor': ['reactflow', '@dagrejs/dagre'],
          'mermaid-vendor': ['mermaid'],
        },
      },
    },
  },
})
