/**
 * AgnesAI 自定义 Provider — pi.dev 扩展
 *
 * 将 AgnesAI（OpenAI 兼容格式）注册为 pi.dev 可用的 LLM 提供商。
 * 放置在 .pi/extensions/agnes-provider/ 目录下，由 DefaultResourceLoader 自动发现。
 *
 * 参考：https://pi.dev/docs/latest/custom-provider
 */
export default function (pi) {
  const baseUrl = process.env.AGNES_API_BASE || 'https://apihub.agnes-ai.cn/v1'
  const modelId = process.env.AGNES_TEXT_MODEL || 'agnes-2.5-flash'

  pi.registerProvider('agnes', {
    name: 'AgnesAI',
    baseUrl,
    apiKey: '$AGNES_API_KEY',
    api: 'openai-completions',
    models: [
      {
        id: modelId,
        name: 'AgnesAI 2.5 Flash',
        reasoning: false,
        input: ['text', 'image'],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 128000,
        maxTokens: 4096,
      },
    ],
  })
}
