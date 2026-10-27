import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from fakes import FakeAnthropic, text
from test_agent import click_script
from test_computer_use import Harness

from jarvis import server
from jarvis.agent import Jarvis


@pytest.fixture
def harness(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    monkeypatch.setattr(server, "jarvis", Jarvis(client=FakeAnthropic([]), computer=h.cu))
    return h


def test_config_has_no_desktop_token(harness):
    c = TestClient(server.app)
    cfg = c.get("/api/config").json()
    assert "test-token" not in c.get("/api/config").text
    assert "propose_desktop_click" in cfg["tools"]
    assert cfg["sandbox_dir"].endswith("JARVIS-Sandbox")


def test_websocket_rejects_other_sites(harness):
    c = TestClient(server.app)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_text()


def receive_until(ws, predicate):
    seen = []
    while True:
        ev = ws.receive_json()
        seen.append(ev)
        if predicate(ev):
            return seen


def test_websocket_approval_flow(harness):
    server.jarvis.client = FakeAnthropic(click_script())
    c = TestClient(server.app)
    with c.websocket_connect("/ws", headers={"origin": "http://127.0.0.1:8765"}) as ws:
        ws.send_json({"type": "ask", "text": "Click OK"})
        seen = receive_until(ws, lambda e: e["type"] == "action" and "approval_token" in e)
        proposal = seen[-1]
        assert any(e["type"] == "screenshot" for e in seen)
        assert harness.desktop.clicks == []
        ws.send_json({"type": "approve", "id": proposal["action"]["id"], "token": "forged"})
        assert receive_until(ws, lambda e: e["type"] == "decision")[-1]["ok"] is False
        ws.send_json({"type": "approve", "id": proposal["action"]["id"], "token": proposal["approval_token"]})
        seen = receive_until(ws, lambda e: e["type"] == "done")
        ws.send_json({"type": "approve", "id": proposal["action"]["id"], "token": proposal["approval_token"]})
        assert receive_until(ws, lambda e: e["type"] == "decision")[-1]["ok"] is False
    assert len(harness.desktop.clicks) == 1
    finals = [e for e in seen if e["type"] == "agent_state" and e["final"]]
    assert finals[-1]["state"] == "COMPLETED"
    history = c.get("/api/actions").json()["actions"]
    assert history[-1]["status"] == "verified"


def test_websocket_cancel(harness):
    server.jarvis.client = FakeAnthropic(click_script())
    c = TestClient(server.app)
    with c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ask", "text": "Click OK"})
        receive_until(ws, lambda e: e["type"] == "action" and "approval_token" in e)
        ws.send_json({"type": "cancel"})
        seen = receive_until(ws, lambda e: e["type"] == "done")
    assert harness.desktop.clicks == []
    assert [e["state"] for e in seen if e["type"] == "agent_state" and e["final"]] == ["CANCELLED"]


def test_plain_chat_over_websocket(harness):
    server.jarvis.client = FakeAnthropic([lambda p: [text("Hello Gill.")]])
    c = TestClient(server.app)
    with c.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ask", "text": "hello"})
        seen = receive_until(ws, lambda e: e["type"] == "done")
    assert {"type": "text", "text": "Hello Gill."} in seen
