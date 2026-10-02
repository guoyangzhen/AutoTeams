/// <reference types="vite/client" />

// WT5: 扩展 Vite 环境变量类型声明（加性追加，不破坏现有声明）
interface ImportMetaEnv {
  /** API 基础路径，默认 /api/v1 */
  readonly VITE_API_BASE_URL?: string
  /** Mock 数据开关，设为 'true' 时前端使用内置 Mock 数据 */
  readonly VITE_USE_MOCK?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

// mermaid 无官方类型声明，补充本项目用到的 API 子集
declare module 'mermaid' {
  interface MermaidConfig {
    startOnLoad?: boolean
    theme?: string
    securityLevel?: string
    fontFamily?: string
    flowchart?: Record<string, unknown>
    themeVariables?: Record<string, unknown>
  }
  interface RenderResult {
    svg: string
  }
  const mermaid: {
    initialize(config: MermaidConfig): void
    render(id: string, text: string): Promise<RenderResult>
    parse?(text: string): Promise<boolean>
  }
  export default mermaid
}
