#!/usr/bin/env node
/**
 * AutoTeams 本地守护进程 bin 入口。
 * 优先加载编译产物 dist/index.js，未编译时回退到 tsx 运行源码。
 */
import { createRequire } from 'module'
import { dirname, join } from 'path'
import { fileURLToPath, pathToFileURL } from 'url'

const require = createRequire(import.meta.url)
const __dirname = dirname(fileURLToPath(import.meta.url))

const distEntry = join(__dirname, '..', 'dist', 'index.js')
const srcEntry = join(__dirname, '..', 'src', 'index.ts')
const root = join(__dirname, '..')

async function main() {
  try {
    // 用 file:// URL 导入，避免 Windows 绝对路径（反斜杠）在动态 import 中解析失败
    await import(pathToFileURL(distEntry).href)
  } catch {
    // 未编译：用 tsx 运行源码
    let tsxPath
    try {
      tsxPath = require.resolve('tsx', { paths: [root] })
    } catch {
      console.error('未找到运行入口 dist/index.js，请在 local-runner 目录先执行 npm run build')
      process.exit(1)
    }
    const { spawnSync } = await import('child_process')
    const result = spawnSync(process.execPath, [tsxPath, srcEntry, ...process.argv.slice(2)], {
      stdio: 'inherit',
    })
    process.exit(result.status ?? 0)
  }
}

main()
