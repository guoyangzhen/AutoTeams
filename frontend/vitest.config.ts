import { defineConfig } from 'vitest/config'
import path from 'path'

// 单元测试配置：只覆盖 src 下的 *.test.ts(x)。
// 使用 jsdom 是因为被测代码依赖 document.cookie（CSRF 读取）与 window 事件。
export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    restoreMocks: true,
  },
})
