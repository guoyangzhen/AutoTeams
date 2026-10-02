// 一次性冒烟配置：仅把 /api 代理指向冒烟后端端口，验证完成后删除。
import base from './vite.config'
import path from 'path'

const target = process.env.SMOKE_API_TARGET || 'http://127.0.0.1:8001'

export default {
  ...base,
  server: {
    ...base.server,
    proxy: {
      ...base.server?.proxy,
      '/api': { target, changeOrigin: true },
    },
  },
  root: path.resolve(__dirname),
}
