import os
import sys
from logging.config import dictConfig

from werkzeug.middleware.proxy_fix import ProxyFix

from drastic_server.app import create_app, socketio

dictConfig(
    {
        "version": 1,
        "formatters": {
            "default": {
                "format": "[%(asctime)s] %(levelname)s in %(module)s: %(message)s",
            }
        },
        "handlers": {
            "wsgi": {
                "class": "logging.StreamHandler",
                "stream": "ext://flask.logging.wsgi_errors_stream",
                "formatter": "default",
            }
        },
        "root": {"level": "INFO", "handlers": ["wsgi"]},
    }
)

app = create_app()
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

if __name__ == "__main__":
    debug = "--debug" in sys.argv or str(os.environ.get("DRASTIC_DEBUG") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        app.extensions["backup_chain_dispatcher"]()

    socketio.run(
        app,
        debug=debug,
        host="0.0.0.0",
        port=app.config.get("BIND_BACKEND_PORT", 5050),
        allow_unsafe_werkzeug=True,
    )
