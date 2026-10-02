/**
 * AutoTeams 桌面端安全预加载脚本 (Preload)。
 * 使用 contextBridge 仅暴露严格白名单的受控 API，严禁前端直接访问 Node.js 原生模块。
 */
import { contextBridge, ipcRenderer } from 'electron'

export interface IElectronAPI {
  // 本地外部 Agent 探针与生命周期
  detectLocalAgents: () => Promise<Array<{ id: string; name: string; type: string; status: string; path?: string }>>
  connectAgent: (agentId: string, options?: Record<string, unknown>) => Promise<{ success: boolean; message: string }>
  sendAgentPrompt: (agentId: string, prompt: string) => Promise<{ reply: string }>
  disconnectAgent: (agentId: string) => Promise<boolean>

  // 本地文件拖拽与安全选择
  openFileDialog: () => Promise<string[]>
  
  // 系统托盘与窗口控制
  minimizeToTray: () => void
  showNotification: (title: string, body: string) => void

  // 监听 Agent 流式输出
  onAgentStream: (callback: (data: { agentId: string; chunk: string }) => void) => () => void
}

const electronAPI: IElectronAPI = {
  detectLocalAgents: () => ipcRenderer.invoke('agent-bridge:detect'),
  connectAgent: (agentId, options) => ipcRenderer.invoke('agent-bridge:connect', { agentId, options }),
  sendAgentPrompt: (agentId, prompt) => ipcRenderer.invoke('agent-bridge:prompt', { agentId, prompt }),
  disconnectAgent: (agentId) => ipcRenderer.invoke('agent-bridge:disconnect', { agentId }),

  openFileDialog: () => ipcRenderer.invoke('fs:open-dialog'),
  minimizeToTray: () => ipcRenderer.send('window:minimize-tray'),
  showNotification: (title, body) => ipcRenderer.send('app:notification', { title, body }),

  onAgentStream: (callback) => {
    const handler = (_event: Electron.IpcRendererEvent, data: { agentId: string; chunk: string }) => callback(data)
    ipcRenderer.on('agent-bridge:stream', handler)
    return () => ipcRenderer.removeListener('agent-bridge:stream', handler)
  },
}

contextBridge.exposeInMainWorld('electronAPI', electronAPI)
