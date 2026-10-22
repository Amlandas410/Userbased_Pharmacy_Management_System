from flask import Flask

from app.auth import auth_bp


def test_dark_mode_is_available_on_sign_in_page():
    app = Flask("app")
    app.secret_key = "test"
    app.register_blueprint(auth_bp)
    app.jinja_env.globals["csrf_token"] = lambda: "test-token"

    with app.test_client() as client:
        response = client.get("/login")

    assert response.status_code == 200
    assert b'class="btn btn-light theme-toggle auth-theme-toggle"' in response.data
    assert b"mediledger-theme" in response.data
    assert b"app.js" in response.data


def test_dark_theme_styles_and_persistent_toggle_are_included():
    app = Flask("app")

    with app.test_client() as client:
        stylesheet = client.get("/static/style.css")
        script = client.get("/static/app.js")

    assert stylesheet.status_code == 200
    assert b':root[data-theme="dark"]' in stylesheet.data
    assert script.status_code == 200
    assert b"localStorage.setItem('mediledger-theme',nextTheme)" in script.data
    assert b"prefers-color-scheme: dark" in script.data
