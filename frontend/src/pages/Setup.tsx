/**
 * Setup — 访谈页面兼容入口（WT5 重构）。
 *
 * PRD §5.7 交互式企业访谈（InterviewPage）是 v3 的配置入口。
 * 原 Setup 向导流程已迁移到：
 * - /interview  交互式企业访谈（7 大类问题 + 完成度更新）
 * - /process/:taskId  LangGraph 构建流程
 *
 * 本页仅作为 `/setup` 路由的兼容入口，重定向到 /interview。
 */
import { Navigate } from 'react-router-dom'

export default function Setup() {
  return <Navigate to="/interview" replace />
}
