from fastapi import FastAPI
from prometheus_client import Counter, Gauge
from prometheus_fastapi_instrumentator import Instrumentator, metrics

BOOKING_OUTCOMES = ("created", "conflict_lock", "conflict_db", "idempotent_replay", "error")
CACHE_OPERATIONS = ("hit", "miss")

booking_requests_total = Counter(
    "booking_requests_total",
    "Booking create requests by outcome",
    ["outcome"],
)
redis_cache_operations_total = Counter(
    "redis_cache_operations_total",
    "Availability cache lookups by result",
    ["operation"],
)
db_pool_checked_out_connections = Gauge(
    "db_pool_checked_out_connections",
    "SQLAlchemy connections currently checked out of the pool",
)
db_pool_size = Gauge("db_pool_size", "Configured SQLAlchemy pool_size")
db_pool_capacity = Gauge("db_pool_capacity", "SQLAlchemy pool_size + max_overflow")
active_websocket_clients = Gauge("active_websocket_clients", "Connected WebSocket clients")
ghost_reclaimed_total = Counter(
    "ghost_reclaimed_total",
    "Bookings auto-released because nobody checked in",
)


def preinitialize_metrics(pool_size: int, max_overflow: int) -> None:
    # touch every label so the series exist at 0, otherwise grafana shows "No data"
    for outcome in BOOKING_OUTCOMES:
        booking_requests_total.labels(outcome=outcome)
    for op in CACHE_OPERATIONS:
        redis_cache_operations_total.labels(operation=op)
    db_pool_size.set(pool_size)
    db_pool_capacity.set(pool_size + max_overflow)


def setup_instrumentator(app: FastAPI) -> None:
    (
        Instrumentator(
            should_instrument_requests_inprogress=False,
            excluded_handlers=["/metrics", "/health"],
        )
        .add(metrics.requests())
        .add(metrics.latency(buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5)))
        .instrument(app)
        .expose(app, endpoint="/metrics", include_in_schema=False)
    )
