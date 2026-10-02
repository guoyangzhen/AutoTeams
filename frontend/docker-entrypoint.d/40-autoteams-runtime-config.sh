#!/bin/sh
# AUD-27：容器启动时用环境变量生成运行时配置 config.json。
#
# 为什么需要：VITE_* 在 `vite build` 时被内联进 JS，Compose 的 environment
# 只影响容器进程，无法改写已编译产物。config.json 则是在容器**启动时**生成的，
# 同一个镜像可以在不同域名 / API 主机下运行。
#
# 环境变量（全部可选，留空即不覆盖）：
#   API_BASE_URL    API 基础地址，如 https://api.example.com/api/v1
#   COLLAB_WS_URL   协作服务 WebSocket，如 wss://collab.example.com/ws
#   ANALYTICS_URL   事件上报地址，缺省复用 API_BASE_URL
#
# 未设置任何变量时写出 {}，前端回退到构建时 VITE_* 与同源 /api/v1。
set -eu

CONFIG_FILE="${AUTOTEAMS_CONFIG_FILE:-/usr/share/nginx/html/config.json}"

json_escape() {
  # 只转义 JSON 字符串里必须转义的字符，其余原样输出。
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

api_base_url="$(json_escape "${API_BASE_URL:-}")"
collab_ws_url="$(json_escape "${COLLAB_WS_URL:-}")"
analytics_url="$(json_escape "${ANALYTICS_URL:-}")"

{
  printf '{'
  first=1
  for pair in "apiBaseUrl:${api_base_url}" "collabWsUrl:${collab_ws_url}" "analyticsUrl:${analytics_url}"; do
    key="${pair%%:*}"
    value="${pair#*:}"
    [ -n "$value" ] || continue
    [ "$first" -eq 1 ] || printf ','
    first=0
    printf '"%s":"%s"' "$key" "$value"
  done
  printf '}\n'
} > "$CONFIG_FILE.tmp"

mv "$CONFIG_FILE.tmp" "$CONFIG_FILE"
chmod 644 "$CONFIG_FILE" 2>/dev/null || true

echo "[autoteams] runtime config written: $(cat "$CONFIG_FILE")"
