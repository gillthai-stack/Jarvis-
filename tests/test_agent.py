import asyncio

from fakes import FakeAnthropic, find, text, tool_use
from test_computer_use import Harness

from jarvis.agent import Jarvis


def click_script(verified=True):
    return [
        lambda p: [tool_use("get_desktop_screenshot", {})],
        lambda p: [tool_use("propose_desktop_click", {"screenshot_id": find(r"screenshot_id: (s_\d+)", p["messages"]),
                                                      "x": 100, "y": 200, "target": "OK button"})],
        lambda p: [tool_use("execute_approved_desktop_click", {"proposal_id": find(r"Proposal (p_\w+)", p["messages"])})],
        lambda p: [tool_use("report_verification", {"action_id": find(r"report_verification for (p_\w+)", p["messages"]),
                                                    "verified": verified, "evidence": "The dialog is gone."})],
        lambda p: [text("Clicked OK and the dialog closed." if verified else "The click did not work.")],
    ]


def test_text_command_click_end_to_end(tmp_path):
    h = Harness(tmp_path)
    fake = FakeAnthropic(click_script())
    jarvis = Jarvis(client=fake, computer=h.cu)

    async def scenario():
        approver = asyncio.create_task(h.approve_when_shown())
        await jarvis.ask("Click OK on that dialog", h.emit)
        await approver

    asyncio.run(scenario())
    assert len(h.desktop.clicks) == 1
    assert h.states()[0] == "PLANNING" and h.states()[-1] == "COMPLETED"
    assert h.events[-1] == {"type": "done"}
    # Every tool Claude can call is one of the typed tools; nothing runs shell commands or code.
    names = {t["name"] for t in fake.calls[0]["tools"]}
    assert {"get_desktop_screenshot", "propose_file_moves", "execute_approved_file_plan", "propose_desktop_click",
            "execute_approved_desktop_click", "propose_keyboard_text", "execute_approved_keyboard_text"} <= names
    assert not names & {"bash", "shell", "run_python", "code_execution"}
    # The approval token never reaches Claude.
    token = h.proposals()[0]["approval_token"]
    assert token not in repr(fake.calls)


def test_failed_verification_stops_and_reports_failed(tmp_path):
    h = Harness(tmp_path)
    fake = FakeAnthropic(click_script(verified=False))
    jarvis = Jarvis(client=fake, computer=h.cu)

    async def scenario():
        asyncio.create_task(h.approve_when_shown())
        await jarvis.ask("Click OK", h.emit)

    asyncio.run(scenario())
    assert h.states()[-1] == "FAILED"
    # After the task stopped, Claude was only allowed to explain, not call tools.
    assert fake.calls[-1]["tool_choice"] == {"type": "none"}


def test_rejected_proposal_stops_task(tmp_path):
    h = Harness(tmp_path)
    steps = click_script()[:3] + [lambda p: [text("Okay, I won't click it.")]]
    fake = FakeAnthropic(steps)
    jarvis = Jarvis(client=fake, computer=h.cu)

    async def scenario():
        asyncio.create_task(h.approve_when_shown(approve=False))
        await jarvis.ask("Click OK", h.emit)

    asyncio.run(scenario())
    assert h.desktop.clicks == []
    assert fake.calls[-1]["tool_choice"] == {"type": "none"}
    assert h.states()[-1] == "CANCELLED"


def test_cancel_mid_task_keeps_history_valid(tmp_path):
    h = Harness(tmp_path)
    fake = FakeAnthropic(click_script() + [lambda p: [text("Hello again.")]])
    jarvis = Jarvis(client=fake, computer=h.cu)

    async def scenario():
        task = asyncio.create_task(jarvis.ask("Click OK", h.emit))
        while not h.proposals():
            await asyncio.sleep(0.01)
        await h.cu.cancel()
        task.cancel()
        await task
        assert jarvis.messages == []  # the half-finished exchange was dropped
        fake.steps = [lambda p: [text("Hello again.")]]
        await jarvis.ask("hi", h.emit)

    asyncio.run(scenario())
    assert h.desktop.clicks == []
    finals = [e["state"] for e in h.events if e["type"] == "agent_state" and e["final"]]
    assert finals == ["CANCELLED", "COMPLETED"]


def test_plain_question_still_works(tmp_path):
    h = Harness(tmp_path)
    fake = FakeAnthropic([lambda p: [tool_use("get_time", {})], lambda p: [text("It's noon.")]])
    jarvis = Jarvis(client=fake, computer=h.cu)
    asyncio.run(jarvis.ask("What time is it?", h.emit))
    assert [e for e in h.events if e["type"] == "text"] == [{"type": "text", "text": "It's noon."}]
    assert h.states() == ["PLANNING", "COMPLETED"]
    assert not any(e["type"] == "error" for e in h.events)
