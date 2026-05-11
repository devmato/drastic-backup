import os


def _env_int(name: str, default: int) -> int:
    value = str(os.environ.get(name, default)).strip()
    try:
        return int(value)
    except ValueError:
        return default


bind = f"0.0.0.0:{_env_int('DRASTIC_BIND_BACKEND_PORT', 5050)}"
workers = max(1, _env_int("DRASTIC_GUNICORN_WORKERS", 1))
worker_class = "gthread"
threads = max(1, _env_int("DRASTIC_GUNICORN_THREADS", 16))
timeout = max(30, _env_int("DRASTIC_GUNICORN_TIMEOUT", 300))
graceful_timeout = max(30, _env_int("DRASTIC_GUNICORN_GRACEFUL_TIMEOUT", 60))
keepalive = max(1, _env_int("DRASTIC_GUNICORN_KEEPALIVE", 5))
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("DRASTIC_GUNICORN_LOG_LEVEL", "info")
