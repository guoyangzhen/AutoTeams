"""应用装配层（core）。

该包只负责「应用外壳」，不包含任何业务逻辑：

- ``lifespan``：进程启动/关闭的上下文管理（依赖自检、后台调度器、文件监听恢复）
- ``middleware``：中间件注册（CORS、限流、CSRF、request_id、缓存头、HTTP 指标）
- ``exceptions``：全局异常处理器，统一错误响应格式
- ``observability``：结构化日志与 Sentry 初始化

业务路由不在这里，全部由 :mod:`app.api` 按业务分域聚合。
"""
