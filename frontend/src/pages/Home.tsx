/**
 * Home — 今日 AI 公司视图入口（WT5 重构）。
 *
 * PRD §6.1 今日 AI 公司视图（AICompanyView）是 v3 的默认首页。
 * 原 Home 控制台功能已分散到：
 * - /company   今日 AI 公司视图（任务流/审批/协作动态/异常告警）
 * - /dashboard 老板运营中心
 * - /canvas    智能体编排
 *
 * 本页仅作为 `/` 路由的兼容入口，重定向到 /company。
 */
import { Navigate } from 'react-router-dom'

export default function Home() {
  return <Navigate to="/company" replace />
}
