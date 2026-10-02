import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 30, // 30 秒内不重复请求
      gcTime: 1000 * 60 * 5, // 5 分钟缓存回收
      retry: (failureCount, error: any) => {
        // 401 / 403 认证错误不重试
        if (error?.response?.status === 401 || error?.response?.status === 403) {
          return false
        }
        return failureCount < 2
      },
      refetchOnWindowFocus: false, // 避免窗口切换频繁打扰
    },
  },
})
