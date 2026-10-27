from fastapi.testclient import TestClient
from PIL import Image

from jarvis.desktop_api import create_app, encode_shot

AUTH = {"Authorization": "Bearer secret"}


class FakeBackend:
    name = "fake"

    def __init__(self):
        self.clicks = []
        self.typed = []

    def screenshot(self):
        return encode_shot(Image.new("RGB", (2880, 1800), "navy"), 1440, 900)

    def click(self, x, y, button, clicks):
        self.clicks.append((x, y, button, clicks))

    def type_text(self, text):
        self.typed.append(text)


def client(backend=None):
    backend = backend or FakeBackend()
    return TestClient(create_app(backend, token="secret", allowed_peers={"testclient"}), base_url="http://127.0.0.1:8766"), backend


def test_requires_token():
    c, _ = client()
    assert c.get("/health").status_code == 401
    assert c.get("/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/health", headers=AUTH).json() == {"ok": True, "backend": "fake"}


def test_rejects_non_local_host_header():
    # A web page using DNS rebinding would arrive with its own host name.
    c = TestClient(create_app(FakeBackend(), token="secret", allowed_peers={"testclient"}), base_url="http://evil.example:8766")
    assert c.get("/health", headers=AUTH).status_code == 403


def test_screenshot_is_downscaled_with_screen_size():
    c, _ = client()
    shot = c.get("/screenshot", headers=AUTH).json()
    assert (shot["width"], shot["height"]) == (1568, 980)
    assert (shot["screen_width"], shot["screen_height"]) == (1440, 900)
    assert len(shot["signature"]) == 32 * 18
    assert shot["id"].startswith("s_")


def test_click_and_type_validated():
    c, b = client()
    assert c.post("/click", json={"x": 10, "y": 20}, headers=AUTH).status_code == 200
    assert b.clicks == [(10, 20, "left", 1)]
    for bad in ({"x": -1, "y": 0}, {"x": 1, "y": 1, "clicks": 3}, {"x": 1, "y": 1, "button": "middle"},
                {"x": 1, "y": 1, "cmd": "rm -rf"}):
        assert c.post("/click", json=bad, headers=AUTH).status_code == 422
    assert c.post("/type", json={"text": "hello"}, headers=AUTH).json() == {"ok": True, "chars": 5}
    assert c.post("/type", json={"text": "line\n"}, headers=AUTH).status_code == 422
    assert c.post("/type", json={"text": "x" * 501}, headers=AUTH).status_code == 422
    assert b.typed == ["hello"]


def test_backend_errors_are_reported():
    class Broken(FakeBackend):
        def click(self, *a):
            raise RuntimeError("no accessibility permission")

    c, _ = client(Broken())
    r = c.post("/click", json={"x": 1, "y": 1}, headers=AUTH)
    assert r.status_code == 500 and "accessibility" in r.json()["detail"]


def test_rejects_non_loopback_peer():
    c = TestClient(create_app(FakeBackend(), token="secret"), base_url="http://127.0.0.1:8766")
    assert c.get("/health", headers=AUTH).status_code == 403  # TestClient's peer is "testclient", not loopback
