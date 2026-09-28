from flask import Flask

from drastic_server.app import _register_spa_routes


def test_root_opens_web_ui(tmp_path):
    (tmp_path / "index.html").write_text("dRastic Backup")
    app = Flask(__name__)
    app.config["SPA_ROOT"] = str(tmp_path)
    _register_spa_routes(app)

    response = app.test_client().get("/", follow_redirects=True)

    assert response.request.path == "/app/"
    assert response.status_code == 200
    assert response.text == "dRastic Backup"
