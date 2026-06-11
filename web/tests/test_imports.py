"""Smoke test: the app entry point must always be importable and serve requests.

This guards the modularization sprint — `from app import app` is the WSGI entry
(Railway/Procfile) and must keep working after every refactor ticket.
"""


def test_app_importable():
    from app import app
    assert app is not None


def test_app_has_test_client():
    from app import app
    client = app.test_client()
    assert client is not None


def test_health_endpoint_responds():
    from app import app
    resp = app.test_client().get("/health")
    assert resp.status_code == 200
