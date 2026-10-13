from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter("pom_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("pom_http_request_seconds", "HTTP request latency", ["route"])
UPSTREAM_CALLS = Counter("pom_upstream_calls_total", "Calls to model and voice providers", ["provider", "kind", "outcome"])
UPSTREAM_LATENCY = Histogram("pom_upstream_seconds", "Upstream latency", ["provider", "kind"])
LLM_TOKENS = Counter("pom_llm_tokens_total", "Language model tokens", ["direction"])
