"""P1-06: Prometheus 指标定义。

指标：
- http_requests_total: HTTP 请求总数（按 method/path/status）
- http_request_duration_seconds: HTTP 请求延迟直方图
- sse_active_streams: 当前活跃 SSE 流式连接数
- llm_tokens_total: LLM token 消耗总量（按 model/direction）

/metrics 端点在 main.py 中注册（仅内网或 admin 访问）。
"""
from prometheus_client import Counter, Histogram, Gauge

# HTTP 请求计数
http_requests_total = Counter(
    "autoteams_http_requests_total",
    "HTTP 请求总数",
    ["method", "path", "status"],
)

# HTTP 请求延迟
http_request_duration_seconds = Histogram(
    "autoteams_http_request_duration_seconds",
    "HTTP 请求延迟（秒）",
    ["method", "path"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# SSE 活跃连接数
sse_active_streams = Gauge(
    "autoteams_sse_active_streams",
    "当前活跃的 SSE 流式连接数",
)

# LLM token 消耗
llm_tokens_total = Counter(
    "autoteams_llm_tokens_total",
    "LLM token 消耗总量",
    ["model", "direction"],  # direction: prompt / completion
)

# BE-SEC-03: 服务端异常计数（按模块/异常类型）
errors_total = Counter(
    "autoteams_errors_total",
    "服务端异常总数",
    ["module", "exception_type"],
)
