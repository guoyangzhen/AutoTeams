# 协作服务：沙箱边界与会话状态存储

本文件记录 `collaboration-service` 目前**实际实现**的隔离手段，以及它们**不是**什么。
面向多租户生产发布前，请先读完「尚未解决」一节。

对应审计项：AUD-02（sandbox 只是 cwd）、AUD-25（首次本地启动失败）。

## 1. 会话工作目录不是安全边界

每个会话有自己的工作目录（新会话默认 `~/.autoteams/workspace/{sessionId}`；已有旧会话继续使用 `~/.autofde/workspace/{sessionId}`，可用
`COLLAB_WORKSPACE_DIR` 覆盖），但 SDK 内置工具把模型给出的路径解析成绝对路径后
直接落盘——cwd 只是相对路径的默认值。审计复现（验证记录 §6.1）：用 SDK 的
`createReadTool(sessionA)` 读取 `../other-session-marker.txt` 成功返回会话外内容。

因此 `src/session-manager.ts` 的 `createSession()` 在 **sandbox 模式**下不再直接注册
SDK 内置工具，而是注册 `src/sandbox-tools.ts` 中同名（`read`/`bash`/`edit`/`write`）
的受控实现。`local` 模式语义不变：仍只注册 `local_fs`，所有文件/命令经 Runner
路由到用户已授权的本地目录。

## 2. 已实现的边界（纵深防御）

| 手段 | 实现 | 覆盖 |
| --- | --- | --- |
| 路径守卫 | `src/path-guard.ts` | read/edit/write（含 mkdir）、知识物化写入 |
| 环境变量白名单 | `src/sandbox-env.ts` | 所有子进程 |
| bash 默认拒绝 | `src/sandbox-tools.ts` | `bash` 工具 |
| 超时 / 输出上限 / 进程回收 | `src/sandbox-tools.ts` | `bash` 工具 |

### 2.1 路径守卫

对每个模型可控路径执行：解析为绝对路径 → 解析符号链接（`fs.realpath`）→
用 `path.relative` 判定仍位于会话根目录内。拒绝 `..` 逃逸、会话目录之外的绝对
路径、以及 realpath 指向外部的符号链接（Windows 用 junction 同样可复现）。
目标尚不存在时（write 新建文件）校验其最近的、已存在的祖先目录。

### 2.2 子进程环境白名单

`buildSandboxEnv()` 只保留 `PATH`/`HOME`/`LANG`/`TMPDIR`/`NODE_ENV` 等子进程真正
需要的变量；`DATABASE_URL`、`*_SECRET*`、`*_API_KEY`、`*_TOKEN*`、`REDIS_URL`、
`CHROMA_*`、bridge 密钥等一律不传递。白名单之外的变量即使名字无害，只要命中
敏感特征模式也会被丢弃。新增任何 `child_process` 调用都必须使用它。

### 2.3 bash 默认拒绝

- `COLLAB_SANDBOX_BASH_ALLOWLIST`：逗号分隔的命令 basename 列表，**默认为空**，
  即默认一个命令都不放行。
- 只接受**单一简单命令**：出现 `;` `&&` `|` `` ` `` `$()` `>` `<` 引号 通配符等
  shell 元字符一律拒绝；`spawn` 使用 `shell: false`。
- 解释器/提权包装器（`sh`/`bash`/`cmd`/`powershell`/`env`/`sudo`/`xargs`/`ssh` …）
  即使写进允许列表也拒绝。
- `COLLAB_SANDBOX_ALLOW_UNRESTRICTED_BASH=true`：跳过上述校验，仅供本地排障，
  开发环境会打印告警；`NODE_ENV=production` 启动时直接拒绝此配置。
- `COLLAB_SANDBOX_BASH_TIMEOUT_MS`（默认 60000，上限 600000）、
  `COLLAB_SANDBOX_BASH_MAX_OUTPUT_BYTES`（默认 262144）强制生效；超限即终止整个
  进程树（POSIX 进程组 / Windows `taskkill /T`），不留下孤儿进程。

生产环境同样拒绝非空 `COLLAB_SANDBOX_BASH_ALLOWLIST`；执行入口还有独立检查，
手工构造策略对象也不能绕过。当前尚无独立隔离执行器，因此生产会话的 bash 能力
保持禁用。开发环境的允许列表只是便利配置，不能作为多租户安全边界。

## 3. 尚未解决：需要 OS 级隔离

以上全部是**纵深防御，不是安全边界**，原因写在代码注释里：

- 路径校验与随后的 `open()`/`write()` 之间存在 TOCTOU 窗口；
- 允许列表里的命令（如 `node`、`python`）本身可以读写服务进程可见的任意文件；
- 服务容器内的网络、内网管理地址、其他会话数据仍在可达范围；
- 没有资源（CPU/内存/进程数/磁盘）与生命周期约束。

**生产上线前必须**把模型可执行工具放进独立的受限容器或微虚拟机：不注入任何服务
凭据，只挂载本租户数据，配置网络出口白名单、cgroups 资源上限与会话级 TTL，
并支持超时/崩溃后的执行环境回收。届时的边界校验放在容器编排层，路径守卫与环境
白名单退化为第二道防线。

## 4. 会话状态存储（AUD-25）

`src/session-store.ts`：

- 加锁前**递归创建**状态文件父目录。旧实现直接 `mkdir(<stateFile>.lock)`，
  全新部署的 `mkdir .../new-data/sessions.json.lock` 报 ENOENT，进程无法启动。
- 锁超时、只读卷（`EROFS`）、无权限（`EACCES`/`EPERM`）、父路径不是目录等
  错误都抛出可区分的 typed error（`SessionStoreLockTimeoutError` /
  `SessionStoreUnavailableError`），不再退化成「空状态」。
- 状态文件损坏（JSON 解析失败、结构不是 `{version:1,sessions:{}}`、空文件）时：
  读路径抛 `SessionStoreCorruptError`，`inspectStoreState()` 返回
  `status: 'corrupt'`，写路径全部被拒绝，并在任何覆盖之前把原文件复制为
  `<stateFile>.corrupt-<时间戳>`。**损坏的历史不会被静默清空。**
- 启动顺序不变：`server.ts` 仍在监听端口前调用 `markActiveSessionsInterrupted()`。
  该调用是写操作，因此状态文件损坏时服务会**明确失败退出**（退出码 1）并打印损坏
  副本路径，而不是带着被覆盖的历史继续运行。恢复方式：检查
  `<stateFile>.corrupt-*`，修复或移走 `COLLAB_STATE_FILE` 后重启。

## 5. 验证

```bash
cd collaboration-service
npm run typecheck
npm run build
npm test
```

`npm test` 使用 Node 内置测试运行器（`node --import tsx --test`），覆盖：首次启动
不再 ENOENT、损坏状态不被覆盖、状态不可写时报可区分错误、路径守卫拒绝
`../`/绝对路径/符号链接逃逸并放行会话内路径、真实子进程环境中不含任何凭据、
bash 默认拒绝与超时/输出上限。


## 2026-09-29 第二轮补验

旧报告把 AUD-02 标为完成，超出了当时证据。完整 OS 隔离仍未实现；本轮只收紧
生产默认和错误配置的行为，不宣称完成多租户执行隔离。修复前，生产策略允许
`node`，真实 `node --version` 子进程仍可执行；两个反例均失败。修复后同样测试
明确拒绝，完整协作服务测试23/23通过。容器文件/网络/资源边界须另行实测。
