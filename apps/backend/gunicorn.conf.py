import os

from drastic_server.config import DefaultConfig, env_int

bind = f"0.0.0.0:{env_int('DRASTIC_BIND_BACKEND_PORT', DefaultConfig.BIND_BACKEND_PORT)}"
workers = env_int("DRASTIC_GUNICORN_WORKERS", DefaultConfig.GUNICORN_WORKERS)
worker_class = "gthread"
threads = env_int("DRASTIC_GUNICORN_THREADS", DefaultConfig.GUNICORN_THREADS)
timeout = max(30, env_int("DRASTIC_GUNICORN_TIMEOUT", DefaultConfig.GUNICORN_TIMEOUT))
graceful_timeout = max(
    30,
    env_int("DRASTIC_GUNICORN_GRACEFUL_TIMEOUT", DefaultConfig.GUNICORN_GRACEFUL_TIMEOUT),
)
keepalive = env_int("DRASTIC_GUNICORN_KEEPALIVE", DefaultConfig.GUNICORN_KEEPALIVE)
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("DRASTIC_GUNICORN_LOG_LEVEL", DefaultConfig.GUNICORN_LOG_LEVEL)
