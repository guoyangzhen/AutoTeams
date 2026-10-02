"""Redis 客户端共享常量。

集中管理 Redis 连接超时参数，避免 6+ 处硬编码 magic number 散落在
cache.py / rate_limit.py / token_blacklist.py / distributed_semaphore.py /
health.py / auth_service.py 中。

设计要点：
- 全部 Redis 客户端复用同一组超时参数，便于运维统一调优；
- 取值偏小（2 秒），目的是在 Redis 不可用时快速回退到内存降级路径，
  避免业务请求被长时间阻塞。
- 若需调整，可在 settings 中新增配置项覆盖（当前未配置，保留默认值）。
"""

# Redis 连接建立超时（秒）
REDIS_SOCKET_CONNECT_TIMEOUT = 2

# Redis 读写操作超时（秒）
REDIS_SOCKET_TIMEOUT = 2
