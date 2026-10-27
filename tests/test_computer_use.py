import asyncio
import json

import pytest

from jarvis.computer_use import Audit, ComputerUse, DesktopError, ToolError, check, TOOLS
from jarvis.sandbox import Sandbox


class FakeDesktop:
    """Stands in for the desktop-control service. `screens` is the signature each screenshot returns."""

    def __init__(self):
        self.n = 0
        self.level = 50
        self.clicks = []
        self.typed = []
        self.after_action = 80  # screen brightness after a click/type, so the change is visible

    def screenshot(self):
        self.n += 1
        return {"id": f"s_{self.n}", "image_base64": "AAAA", "media_type": "image/jpeg", "width": 1568,
                "height": 980, "screen_width": 1440.0, "screen_height": 900.0, "signature": [self.level] * 576}

    def click(self, x, y, button, clicks):
        self.clicks.append((x, y, button, clicks))
        self.level = self.after_action
        return {"ok": True}

    def type_text(self, text):
        self.typed.append(text)
        self.level = self.after_action
        return {"ok": True}


class Harness:
    def __init__(self, tmp_path, approval_timeout=5):
        self.events = []
        self.desktop = FakeDesktop()
        root = tmp_path / "JARVIS-Sandbox"
        root.mkdir()
        self.audit_path = tmp_path / "audit.jsonl"
        self.cu = ComputerUse(self.desktop, Sandbox(root), Audit(self.audit_path),
                              approval_timeout=approval_timeout, settle=0)

    async def emit(self, ev):
        self.events.append(ev)

    def proposals(self):
        return [e for e in self.events if e["type"] == "action" and "approval_token" in e]

    def states(self):
        return [e["state"] for e in self.events if e["type"] == "agent_state"]

    async def approve_when_shown(self, approve=True, delay=0.01):
        while not self.proposals():
            await asyncio.sleep(delay)
        p = self.proposals()[-1]
        return await self.cu.decide(p["action"]["id"], p["approval_token"], approve)


def run(coro):
    return asyncio.run(coro)


def test_click_flow_requires_approval_and_verifies(tmp_path):
    h = Harness(tmp_path)

    async def scenario():
        await h.cu.begin_task("click the OK button", h.emit)
        shot = await h.cu.run("get_desktop_screenshot", {})
        assert shot[0]["type"] == "image" and "untrusted" in shot[1]["text"]
        msg = await h.cu.run("propose_desktop_click", {"screenshot_id": "s_1", "x": 784, "y": 490, "target": "OK button"})
        pid = msg.split()[1]
        assert h.desktop.clicks == []  # nothing happens on proposal
        approver = asyncio.create_task(h.approve_when_shown())
        result = await h.cu.run("execute_approved_desktop_click", {"proposal_id": pid})
        assert (await approver)[0]
        # 784/1568 * 1440 points
        assert h.desktop.clicks == [(720.0, 450.0, "left", 1)]
        assert result[0]["type"] == "image" and "report_verification" in result[1]["text"]
        await h.cu.run("report_verification", {"action_id": pid, "verified": True, "evidence": "Dialog closed."})
        return await h.cu.finish_task()

    assert run(scenario()) == "COMPLETED"
    assert h.states() == ["PLANNING", "AWAITING_APPROVAL", "AWAITING_APPROVAL", "EXECUTING", "VERIFYING",
                          "PLANNING", "COMPLETED"]
    # The approval token went to the dashboard, never into anything returned to Claude.
    token = h.proposals()[0]["approval_token"]
    assert token not in h.audit_path.read_text()


def test_unverified_action_fails_task(tmp_path):
    h = Harness(tmp_path)

    async def scenario():
        await h.cu.begin_task("type my code", h.emit)
        await h.cu.run("get_desktop_screenshot", {})
        pid = (await h.cu.run("propose_keyboard_text", {"screenshot_id": "s_1", "text": "zq-4417", "target": "search"})).split()[1]
        asyncio.create_task(h.approve_when_shown())
        await h.cu.run("execute_approved_keyboard_text", {"proposal_id": pid})
        return await h.cu.finish_task()

    assert run(scenario()) == "FAILED"
    assert h.desktop.typed == ["zq-4417"]
    assert "zq-4417" not in h.audit_path.read_text()  # typed text is redacted
    assert "<7 characters>" in h.audit_path.read_text()
    assert h.proposals()[0]["action"]["display"]["text"] == "zq-4417"  # but shown in full for approval


def test_cancel_stops_task(tmp_path):
    h = Harness(tmp_path)

    async def scenario():
        await h.cu.begin_task("click", h.emit)
        await h.cu.run("get_desktop_screenshot", {})
        pid = (await h.cu.run("propose_desktop_click", {"screenshot_id": "s_1", "x": 1, "y": 1, "target": "x"})).split()[1]
        asyncio.create_task(h.approve_when_shown(approve=False))
        with pytest.raises(ToolError, match="did not approve"):
            await h.cu.run("execute_approved_desktop_click", {"proposal_id": pid})
        with pytest.raises(ToolError, match="stopped"):
            await h.cu.run("get_desktop_screenshot", {})
        return await h.cu.finish_task()

    assert run(scenario()) == "CANCELLED"
    assert h.desktop.clicks == []


def test_stop_button_cancels(tmp_path):
    h = Harness(tmp_path)

    async def scenario():
        await h.cu.begin_task("click", h.emit)
        await h.cu.run("get_desktop_screenshot", {})
        pid = (await h.cu.run("propose_desktop_click", {"screenshot_id": "s_1", "x": 1, "y": 1, "target": "x"})).split()[1]
        waiter = asyncio.create_task(h.cu.run("execute_approved_desktop_click", {"proposal_id": pid}))
        await asyncio.sleep(0.01)
        await h.cu.cancel()
        with pytest.raises(ToolError):
            await waiter
        return await h.cu.finish_task()

    assert run(scenario()) == "CANCELLED"
    assert h.desktop.clicks == []


def test_approval_timeout(tmp_path):
    h = Harness(tmp_path, approval_timeout=0.05)

    async def scenario():
        await h.cu.begin_task("click", h.emit)
        await h.cu.run("get_desktop_screenshot", {})
        pid = (await h.cu.run("propose_desktop_click", {"screenshot_id": "s_1", "x": 1, "y": 1, "target": "x"})).split()[1]
        with pytest.raises(ToolError, match="expired"):
            await h.cu.run("execute_approved_desktop_click", {"proposal_id": pid})
        return await h.cu.finish_task()

    assert run(scenario()) == "FAILED"
    assert h.desktop.clicks == []


def test_repeated_and_forged_approvals_rejected(tmp_path):
    h = Harness(tmp_path)

    async def scenario():
        await h.cu.begin_task("click", h.emit)
        await h.cu.run("get_desktop_screenshot", {})
        pid = (await h.cu.run("propose_desktop_click", {"screenshot_id": "s_1", "x": 1, "y": 1, "target": "x"})).split()[1]
        token = h.proposals()[0]["approval_token"]
        assert (await h.cu.decide(pid, "guess", True))[0] is False
        assert (await h.cu.decide(pid, token, True))[0] is True
        assert (await h.cu.decide(pid, token, True)) == (False, "That proposal is already approved.")
        await h.cu.run("execute_approved_desktop_click", {"proposal_id": pid})
        # Executing the same approval again does nothing.
        with pytest.raises(ToolError):
            await h.cu.run("execute_approved_desktop_click", {"proposal_id": pid})
        assert (await h.cu.decide(pid, token, True))[0] is False

    run(scenario())
    assert len(h.desktop.clicks) == 1
    assert "decision_rejected" in h.audit_path.read_text()


def test_unexpected_screen_change_aborts(tmp_path):
    h = Harness(tmp_path)

    async def scenario():
        await h.cu.begin_task("click", h.emit)
        await h.cu.run("get_desktop_screenshot", {})
        pid = (await h.cu.run("propose_desktop_click", {"screenshot_id": "s_1", "x": 1, "y": 1, "target": "x"})).split()[1]
        h.desktop.level = 200  # a different window came to the front
        asyncio.create_task(h.approve_when_shown())
        with pytest.raises(ToolError, match="screen changed"):
            await h.cu.run("execute_approved_desktop_click", {"proposal_id": pid})
        return await h.cu.finish_task()

    assert run(scenario()) == "FAILED"
    assert h.desktop.clicks == []


def test_click_needs_latest_screenshot_and_bounds(tmp_path):
    h = Harness(tmp_path)

    async def scenario():
        await h.cu.begin_task("click", h.emit)
        await h.cu.run("get_desktop_screenshot", {})
        await h.cu.run("get_desktop_screenshot", {})
        with pytest.raises(ToolError, match="latest"):
            await h.cu.run("propose_desktop_click", {"screenshot_id": "s_1", "x": 1, "y": 1, "target": "x"})
        with pytest.raises(ToolError, match="outside"):
            await h.cu.run("propose_desktop_click", {"screenshot_id": "s_2", "x": 5000, "y": 1, "target": "x"})
        with pytest.raises(ToolError, match="Unexpected field"):
            await h.cu.run("propose_desktop_click", {"screenshot_id": "s_2", "x": 1, "y": 1, "target": "x",
                                                     "command": "rm -rf ~"})
        with pytest.raises(ToolError, match="control characters"):
            await h.cu.run("propose_keyboard_text", {"screenshot_id": "s_2", "text": "a\nb", "target": "x"})

    run(scenario())


def test_file_plan_flow(tmp_path):
    h = Harness(tmp_path)
    (h.cu.sandbox.root / "a.jpg").write_bytes(b"1")
    (h.cu.sandbox.root / "b.pdf").write_bytes(b"22")

    async def scenario():
        await h.cu.begin_task("tidy the sandbox", h.emit)
        listing = json.loads(await h.cu.run("list_sandbox_files", {}))
        assert [f["path"] for f in listing["files"]] == ["a.jpg", "b.pdf"]
        msg = await h.cu.run("propose_file_moves", {"reason": "Sort by type"})
        pid = msg.split()[1]
        shown = h.proposals()[0]["action"]["display"]["moves"]
        assert shown == [{"source": "a.jpg", "destination": "Images/a.jpg"},
                         {"source": "b.pdf", "destination": "Documents/b.pdf"}]
        assert (h.cu.sandbox.root / "a.jpg").exists()  # nothing moved yet
        asyncio.create_task(h.approve_when_shown())
        result = await h.cu.run("execute_approved_file_plan", {"proposal_id": pid})
        assert "Disk check: every moved file" in result
        await h.cu.run("report_verification", {"action_id": pid, "verified": True, "evidence": "Disk check passed."})
        return await h.cu.finish_task()

    assert run(scenario()) == "COMPLETED"
    assert (h.cu.sandbox.root / "Images/a.jpg").exists() and (h.cu.sandbox.root / "Documents/b.pdf").exists()


def test_file_plan_outside_sandbox_refused(tmp_path):
    h = Harness(tmp_path)

    async def scenario():
        await h.cu.begin_task("move my documents", h.emit)
        with pytest.raises(ToolError, match="Plan refused"):
            await h.cu.run("propose_file_moves", {"reason": "x", "moves": [
                {"source": str(tmp_path / "audit.jsonl"), "destination": "audit.jsonl"}]})

    run(scenario())
    assert h.proposals() == []


def test_cannot_claim_success_when_disk_check_fails(tmp_path):
    h = Harness(tmp_path)
    (h.cu.sandbox.root / "a.txt").write_text("x")

    async def scenario():
        await h.cu.begin_task("move", h.emit)
        pid = (await h.cu.run("propose_file_moves", {"reason": "x", "moves": [
            {"source": "a.txt", "destination": "b.txt"}]})).split()[1]
        (h.cu.sandbox.root / "b.txt").write_text("appeared meanwhile")
        asyncio.create_task(h.approve_when_shown())
        result = await h.cu.run("execute_approved_file_plan", {"proposal_id": pid})
        assert "Disk check FAILED" in result
        with pytest.raises(ToolError, match="cannot be reported as verified"):
            await h.cu.run("report_verification", {"action_id": pid, "verified": True, "evidence": "Looks fine"})
        return await h.cu.finish_task()

    assert run(scenario()) == "FAILED"
    assert (h.cu.sandbox.root / "b.txt").read_text() == "appeared meanwhile"


def test_desktop_service_down(tmp_path):
    h = Harness(tmp_path)

    def down():
        raise DesktopError("The desktop-control service isn't running.")

    h.desktop.screenshot = down

    async def scenario():
        await h.cu.begin_task("look", h.emit)
        with pytest.raises(ToolError, match="isn't running"):
            await h.cu.run("get_desktop_screenshot", {})

    run(scenario())


def test_schema_check():
    schema = TOOLS["propose_file_moves"]["input_schema"]
    check(schema, {"reason": "x", "moves": [{"source": "a", "destination": "b"}]})
    for bad in ({}, {"reason": 3}, {"reason": "x", "moves": [{"source": "a"}]}, {"reason": "x", "moves": "a"}):
        with pytest.raises(ToolError):
            check(schema, bad)
    with pytest.raises(ToolError):
        check(TOOLS["propose_desktop_click"]["input_schema"],
              {"screenshot_id": "s", "x": True, "y": 1, "target": "t"})
