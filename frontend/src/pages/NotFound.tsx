import { useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/Button'
import { Home, ArrowLeft } from 'lucide-react'

/**
 * P1-FE: 独立 404 页面
 *
 * 将未知路径从「自动跳回首页」改为展示明确的未找到提示，
 * 帮助用户意识到 URL 错误，并提供返回首页/上一页的快捷操作。
 */
export default function NotFound() {
  const navigate = useNavigate()

  return (
    <div className="min-h-screen flex items-center justify-center bg-canvas p-4">
      <div className="max-w-md w-full text-center">
        <div className="mb-6">
          <span className="text-8xl font-bold text-brand-500/20">404</span>
        </div>
        <h1 className="text-2xl font-semibold text-text-primary mb-2">
          页面不存在
        </h1>
        <p className="text-sm text-text-secondary mb-8">
          你访问的地址可能已被移除、更名，或暂时不可用。
        </p>
        <div className="flex gap-3 justify-center">
          <Button variant="outline" size="sm" onClick={() => navigate(-1)}>
            <ArrowLeft className="w-4 h-4 mr-1.5" />
            返回上一页
          </Button>
          <Button variant="primary" size="sm" onClick={() => navigate('/')}>
            <Home className="w-4 h-4 mr-1.5" />
            回到首页
          </Button>
        </div>
      </div>
    </div>
  )
}
