# 前端 UI 细化覆盖层 (D5)

本目录承载 D5「前端 UI 细化与移动端适配」的 CSS Override Layer。

## 接入方式

`main.tsx` 在 `import './index.css'` 之后引入：

```ts
import './styles/refinement.css'
```

移除该行即可整体回退到覆盖前状态（零侵入、可回退）。

## 原理

Tailwind v3 的 `@tailwind utilities;` 将工具类放入 `utilities` cascade layer。
按浏览器 cascade-layer 规范，**未分层的普通 CSS 优先级高于任何 @layer 规则**，
因此 `refinement.css` 中的普通选择器无需 `!important` 即可覆盖 Tailwind 工具类。

## FIX 落地清单

### refinement.css 覆盖

| FIX | 规则 | 选择器 |
|-----|------|--------|
| FIX-03 | 空状态图标 48px/rounded-xl | `.w-14.h-14.rounded-2xl` |
| FIX-04 | 焦点态 ring-2 ring-brand-500/20 | `.settings-input:focus`、`input[class*="focus:shadow-focus"]:focus`、`input[class*="ring-brand-500/12"]:focus` |
| FIX-06 | 区块标题 text-lg | `h2.font-serif-display.text-xl` |
| FIX-07 | 工作流按钮组对齐 | `.wf-ctrl-btn`、`.wf-ctrl-link` |
| FIX-12 | brand-rule 间距 mt-3 mb-4 | `.brand-rule` |
| FIX-13 | Card 内边距 p-5 | `.rounded-xl.shadow-soft.p-6` |
| S14 | 移动端 375px 防溢出 | `@media (max-width:640px)` |

### 组件内直改（CSS 无法可靠覆盖）

| FIX | 文件 | 改动 |
|-----|------|------|
| FIX-01 | AgentCard/SkillCard/KnowledgePage | 图标容器 48px→36px (w-9 h-9 rounded-md p-2) |
| FIX-02 | AgentCard/SkillCard | 卡片图标 24px→20px (w-5 h-5) |
| FIX-05 | Home.tsx | 页面标题 text-4xl→text-3xl |
| FIX-08 | Home.tsx | 删除"管理"按钮 hover:gap-1 |
| FIX-11 | Settings.tsx | ToggleSwitch 滑块 16px→18px + 行程调整 |
| FIX-14 | AgentCard/SkillCard | Card 加 shadow-soft |
| FIX-15 | Home.tsx | ready 状态文字色 → text-success |
| FIX-16 | Login.tsx | Eye/EyeOff 图标 20px→16px |
| FIX-17 | Home.tsx | 流水线 pending 圆圈加 border-dashed |
| FIX-19 | KnowledgePage.tsx | 表格操作按钮 w-7→w-8 (32px) |
| FIX-20 | Home.tsx | section header 图标 20px→16px |
| S14 | Home.tsx | 工作流 SVG 去 minWidth:600 |
| S14 | KnowledgePage.tsx | 文件表格移动端卡片视图 |
| S14 | LoopDashboard.tsx | 图表容器 h-[260px]→min-h-[260px] |

### 不实施（归 D2 / Chat.tsx 独占）

- FIX-09 Chat 消息操作按钮
- FIX-18 Chat 侧边栏技能标签内边距

## 边界

- 不改 `Chat.tsx`、`Process.tsx`、`AgentCanvasPage.tsx`（D2/D3 独占）
- 不改 `frontend/src/api/**`、`package.json`、`backend/**`
- 不新增 npm 依赖
