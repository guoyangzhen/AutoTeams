#!/usr/bin/env bash
# 将镜像内干净预设(v1.6.0)复制进持久卷，版本号提升为 v1.9.0 强制新建激活版本，
# 覆盖工具注册表(13 个全安装)、干净组织架构(8 部门)、唯一命名流程引擎(9 个)。
set -euo pipefail

IMG=ghcr.io/guoyangzhen/autoteams-backend:latest
C=autoteams-deploy-backend-1
cd ~/autoteams-deploy

echo "==> 1. 从镜像导出干净预设"
sudo docker run --rm --entrypoint cat "$IMG" /app/data/demo_runtime_preset.json > /tmp/demo_runtime_preset.json

echo "==> 2. 覆盖持久卷预设文件"
sudo docker cp /tmp/demo_runtime_preset.json "$C":/app/data/demo_runtime_preset.json

echo "==> 3. 版本号提升为 v1.9.0"
sudo docker exec "$C" python -c 'import json,io; p="/app/data/demo_runtime_preset.json"; d=json.load(io.open(p,encoding="utf-8")); d["version"]="v1.9.0"; json.dump(d,io.open(p,"w",encoding="utf-8"),ensure_ascii=False,indent=2); print("bumped to",d["version"])'

echo "==> 4. 重新 seed demo 运行时"
sudo docker exec "$C" python -m scripts.seed_demo_runtime

echo "==> 完成"
