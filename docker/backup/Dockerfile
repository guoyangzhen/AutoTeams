# ============================================================
# AutoTeams 备份容器镜像
# 基于 postgres:15-alpine（自带 pg_dump/pg_isready/gzip/tar/sha256sum）
# 额外安装 gnupg 以支持 BACKUP_ENCRYPTION_KEY 的 GPG/AES256 加密
# ============================================================
FROM postgres:15-alpine

# 安装 gpg（PGP 对称加密）与 bash
RUN apk add --no-cache gnupg bash

# 拷贝容器内备份脚本
COPY scripts/backup_container.sh /usr/local/bin/backup.sh
RUN chmod +x /usr/local/bin/backup.sh \
    && sed -i 's/\r$//' /usr/local/bin/backup.sh

ENTRYPOINT ["/usr/local/bin/backup.sh"]