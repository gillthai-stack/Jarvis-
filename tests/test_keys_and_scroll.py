"""Key presses, shortcuts and scrolling: validated, approval-gated, and never into a terminal."""

import asyncio

import pytest
from test_computer_use import FakeDesktop, Harness
from test_desktop_api import AUTH, FakeBackend, client

from jarvis.desktop_api import parse_keys


class KeyBackend(FakeBackend):
    name = "macos"

    def __init__(self, front="com.apple.Safari"):
        super().__init__()
        self.front = front
        self.keys = []
        self.scrolls = []

    def press_key(self, key, modifiers):
        self.keys.append((key, modifiers))

    def scroll(self, x, y, lines):
        self.scrolls.append((x, y, lines))

    def frontmost_app(self):
        return self.front


def test_parse_keys():
    assert parse_keys("enter") == ("return", [])
    assert parse_keys("Cmd+Shift+T") == ("t", ["cmd", "shift"])
    assert parse_keys("command + l") == ("l", ["cmd"])
    for bad in ("", "cmd", "cmd+", "hyper+t", "cmd+shift+delete", "cmd+option+esc", "cmd+shift+q", "f13",
                "a+b+c+d+e"):
        with pytest.raises(ValueError):
            parse_keys(bad)


def test_key_scroll_and_type_enter_endpoints():
    b = KeyBackend()
    c, _ = client(b)
    assert c.post("/key", json={"keys": "cmd+t"}, headers=AUTH).status_code == 200
    assert c.post("/key", json={"keys": "cmd+shift+delete"}, headers=AUTH).status_code == 422
    assert c.post("/type", json={"text": "weather", "press_enter": True}, headers=AUTH).status_code == 200
    assert c.post("/scroll", json={"x": 10, "y": 20, "lines": 5}, headers=AUTH).status_code == 200
    assert c.post("/scroll", json={"x": 10, "y": 20, "lines": 500}, headers=AUTH).status_code == 422
    assert b.keys == [("t", ["cmd"]), ("return", [])] and b.typed == ["weather"] and b.scrolls == [(10, 20, 5)]


@pytest.mark.parametrize("front", ["com.apple.Terminal", "com.googlecode.iterm2"])
def test_never_types_or_presses_keys_into_a_terminal(front):
    b = KeyBackend(front)
    c, _ = client(b)
    assert c.post("/key", json={"keys": "enter"}, headers=AUTH).status_code == 403
    assert c.post("/type", json={"text": "ls"}, headers=AUTH).status_code == 403
    assert b.keys == [] and b.typed == []


def test_unknown_front_app_blocks_enter_but_not_plain_typing():
    b = KeyBackend(front=None)
    c, _ = client(b)
    assert c.post("/key", json={"keys": "enter"}, headers=AUTH).status_code == 403
    assert c.post("/type", json={"text": "hi", "press_enter": True}, headers=AUTH).status_code == 403
    assert c.post("/type", json={"text": "hi"}, headers=AUTH).status_code == 200


class KeyDesktop(FakeDesktop):
    def __init__(self):
        super().__init__()
        self.keys = []
        self.scrolls = []
        self.entered = []

    def type_text(self, text, press_enter=False):
        self.entered.append(press_enter)
        return super().type_text(text)

    def press_key(self, keys):
        self.keys.append(keys)
        self.level = self.after_action
        return {"ok": True}

    def scroll(self, x, y, lines):
        self.scrolls.append((x, y, lines))
        self.level = self.after_action
        return {"ok": True}


def flow(h, propose, args, execute):
    async def scenario():
        await h.cu.begin_task("do it", h.emit)
        await h.cu.run("get_desktop_screenshot", {})
        msg = await h.cu.run(propose, {"screenshot_id": "s_1", **args})
        pid = msg.split()[1]
        approver = asyncio.create_task(h.approve_when_shown())
        await h.cu.run(execute, {"proposal_id": pid})
        await approver
        return pid
    return asyncio.run(scenario())


def test_key_press_needs_approval(tmp_path):
    h = Harness(tmp_path)
    h.desktop = h.cu.desktop = KeyDesktop()
    flow(h, "propose_key_press", {"keys": "Cmd+T", "purpose": "open a new tab"}, "execute_approved_key_press")
    assert h.desktop.keys == ["cmd+t"]
    assert h.proposals()[0]["action"]["summary"] == "Press cmd+t: open a new tab"


def test_blocked_shortcut_is_refused_at_proposal(tmp_path):
    from jarvis.computer_use import ToolError
    h = Harness(tmp_path)

    async def scenario():
        await h.cu.begin_task("empty the trash", h.emit)
        await h.cu.run("get_desktop_screenshot", {})
        with pytest.raises(ToolError):
            await h.cu.run("propose_key_press", {"screenshot_id": "s_1", "keys": "cmd+shift+delete", "purpose": "x"})
    asyncio.run(scenario())
    assert h.proposals() == []


def test_scroll_converts_to_points(tmp_path):
    h = Harness(tmp_path)
    h.desktop = h.cu.desktop = KeyDesktop()
    flow(h, "propose_scroll", {"x": 784, "y": 490, "lines": 8, "target": "the page"}, "execute_approved_scroll")
    assert h.desktop.scrolls == [(720.0, 450.0, 8)]


def test_type_then_enter(tmp_path):
    h = Harness(tmp_path)
    h.desktop = h.cu.desktop = KeyDesktop()
    flow(h, "propose_keyboard_text", {"text": "weather in Paris", "target": "search box", "press_enter": True},
         "execute_approved_keyboard_text")
    assert h.desktop.typed == ["weather in Paris"] and h.desktop.entered == [True]
    assert h.proposals()[0]["action"]["summary"].endswith("and press Enter")
