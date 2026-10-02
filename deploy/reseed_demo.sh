#!/usr/bin/env bash
# 将新镜像内的 demo_runtime_preset.json (v1.6.0) 复制进 /app/data 持久卷，
# 然后重新 seed demo 企业运行时（导入 v1.6.0 为激活版本）。
set -euo pipefail

IMG=ghcr.io/guoyangzhen/autoteams-backend:latest
C=autoteams-deploy-backend-1
cd ~/autoteams-deploy

echo "==> 1. 从镜像导出 v1.6.0 预设"
sudo docker run --rm --entrypoint cat "$IMG" /app/data/demo_runtime_preset.json > /tmp/demo_runtime_preset.json

echo "==> 2. 覆盖持久卷中的预设文件"
sudo docker cp /tmp/demo_runtime_preset.json "$C":/app/data/demo_runtime_preset.json

echo "==> 3. 重新 seed demo 运行时"
sudo docker exec "$C" python -m scripts.seed_demo_runtime

echo "==> 完成"
