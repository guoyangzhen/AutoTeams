# ============================================================
# AutoTeams 协作服务镜像（A2）
# 基于 pi.dev SDK + AgnesAI 的 Node.js/TypeScript 服务
# 对外提供 HTTP /api/* 与 WebSocket /ws，由 frontend nginx 反向代理
# ============================================================

# --- 构建阶段 ---
FROM node:22-alpine AS build
WORKDIR /app

# 复制依赖清单（先装依赖，利用 Docker 层缓存）
COPY package.json package-lock.json ./
RUN npm ci

# 复制源码与 pi.dev 扩展（扩展在运行时通过 cwd/.pi/extensions 发现）
COPY src ./src
COPY .pi ./pi-ext
COPY tsconfig.json ./

# 编译 TypeScript → dist/
RUN npm run build

# --- 运行时阶段 ---
# 使用 Node 22（LTS）：package.json overrides 将 undici 锁定为 ^8.10.0，
# 而 undici 8.0.3+ 最低要求 Node >=22.19.0（markAsUncloneable API），
# 在 Node 20 下容器启动即崩溃（webidl.util.markAsUncloneable is not a function）。
FROM node:22-alpine
WORKDIR /app

# 非 root 运行（最小权限）
RUN addgroup -S app && adduser -S app -G app

# 仅复制生产依赖（--omit=dev），减小体积
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# 复制编译产物与 pi.dev 扩展
COPY --from=build /app/dist ./dist
# 将 pi.dev 扩展放到进程工作目录的 .pi/extensions 下，供 SDK 自动发现
COPY --from=build /app/pi-ext ./.pi

ENV NODE_ENV=production
ENV PORT=3001

# 会话元数据与历史写入专用数据卷，避免重启后丢失用户可见状态。
RUN mkdir -p /app/data && chown -R app:app /app/data

USER app
EXPOSE 3001

CMD ["node", "dist/server.js"]
