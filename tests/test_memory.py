import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from fakes import FakeAnthropic, text, tool_use
from test_computer_use import Harness

from jarvis import agent, memory, server, tools
from jarvis.agent import Jarvis
from jarvis.memory import MemoryStore, SecretError, find_secret, redact


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = MemoryStore(tmp_path / "jarvis.db")
    monkeypatch.setattr(memory, "store", s)
    yield s
    s.close()


def ask(jarvis, prompt, h):
    asyncio.run(jarvis.ask(prompt, h.emit))


# ---------- secrets ----------

@pytest.mark.parametrize("secret", [
    "my anthropic key is sk-ant-api03-abcdefghijklmnop",
    "My password is hunter22",
    "the wifi passcode: Tr0ub4dor",
    "PIN: 4821",
    "card 4242 4242 4242 4242 exp 12/28",
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----",
    "SSN 123-45-6789",
])
def test_secrets_are_detected(secret):
    assert find_secret(secret)


@pytest.mark.parametrize("fine", [
    "Gill likes tea with oat milk",
    "Gill uses 1Password as their password manager",
    "The Jarvis repo is gillthai-stack/Jarvis-",
    "Order number 1234 5678 9012 3456",  # 16 digits but not a valid card number
    "Meeting at 10:30 on 2026-10-27",
])
def test_ordinary_facts_are_not_secrets(fine):
    assert find_secret(fine) is None


def test_redact_keeps_the_sentence_and_masks_the_secret():
    out = redact("Log in with password is hunter22 and key sk-ant-api03-abcdefghijklmnop please")
    assert "hunter22" not in out and "sk-ant" not in out
    assert out.startswith("Log in with password is [password or code removed]")
    assert out.endswith("please")


# ---------- facts ----------

def test_suggested_facts_wait_for_approval_and_user_facts_dont(store):
    suggested = store.add_fact("Gill drinks green tea", source="assistant")
    added = store.add_fact("Gill's dog is called Max", project="home")
    assert suggested["status"] == "pending" and added["status"] == "approved"
    assert [f["text"] for f in store.approved_facts()] == ["Gill's dog is called Max"]
    # Pending ones are listed first so they get reviewed.
    assert store.list_facts()[0]["id"] == suggested["id"]
    store.update_fact(suggested["id"], approve=True)
    assert {f["text"] for f in store.approved_facts()} == {"Gill drinks green tea", "Gill's dog is called Max"}


def test_editing_a_suggestion_approves_it_and_secrets_are_refused(store):
    f = store.add_fact("Gill likes jazz", source="assistant")
    edited = store.update_fact(f["id"], text="Gill likes modal jazz")
    assert edited["status"] == "approved" and edited["text"] == "Gill likes modal jazz"
    with pytest.raises(SecretError):
        store.update_fact(f["id"], text="Gill's password is hunter22")
    with pytest.raises(SecretError):
        store.add_fact("bank PIN: 4821")
    assert store.get_fact(f["id"])["text"] == "Gill likes modal jazz"
    assert store.delete_fact(f["id"]) and store.get_fact(f["id"]) is None
    assert not store.delete_fact(f["id"])


def test_duplicate_suggestion_is_not_added_twice(store):
    a = store.add_fact("Gill likes tea", source="assistant")
    b = store.add_fact("gill likes tea", source="assistant")
    assert a["id"] == b["id"] and len(store.list_facts()) == 1


def test_old_memory_json_is_imported_once_without_secrets(tmp_path):
    legacy = tmp_path / "memory.json"
    legacy.write_text(json.dumps([{"note": "Gill likes tea", "saved": "2026-10-26 09:00"},
                                  {"note": "Gill's password is hunter22", "saved": "2026-10-26 09:01"}]))
    s = MemoryStore(tmp_path / "jarvis.db", legacy)
    assert [(f["text"], f["status"], f["source"]) for f in s.list_facts()] == [("Gill likes tea", "approved", "imported")]
    assert not legacy.exists() and (tmp_path / "memory.json.imported").exists()
    s.close()
    legacy.write_text(json.dumps([{"note": "again"}]))
    s = MemoryStore(tmp_path / "jarvis.db", legacy)
    assert len(s.list_facts()) == 1
    s.close()


# ---------- conversation history ----------

def test_history_is_searchable_masks_secrets_and_survives_restart(tmp_path):
    s = MemoryStore(tmp_path / "jarvis.db")
    cid = s.add_exchange(None, "What's my token is abc123secret for the router?", "I won't keep that.")
    s.add_exchange(cid, "Remind me about the Lisbon trip", "Your Lisbon flight is on Friday.")
    s.close()
    s = MemoryStore(tmp_path / "jarvis.db")
    hits = s.search_messages("lisbon")
    assert {h["role"] for h in hits} == {"user", "assistant"} and all(h["conversation_id"] == cid for h in hits)
    assert s.search_messages("lisb fri")  # word prefixes, every word must match
    assert s.search_messages('") OR 1=1 --') == []  # query text never becomes FTS or SQL syntax
    conv = s.get_conversation(cid)
    assert "abc123secret" not in json.dumps(conv) and "[password or code removed]" in conv["messages"][0]["text"]
    assert s.list_conversations()[0]["first"].startswith("What's my token")
    assert s.delete_conversation(cid) and s.search_messages("lisbon") == [] and s.get_conversation(cid) is None
    # Saving into a deleted conversation starts a fresh one instead of failing.
    assert s.add_exchange(cid, "hi", "hello") != cid
    s.close()


def test_agent_saves_each_finished_exchange_and_reset_starts_a_new_conversation(tmp_path, store):
    h = Harness(tmp_path)
    fake = FakeAnthropic([lambda p: [text("Hello, Gill.")], lambda p: [text("It's sunny.")],
                          lambda p: [text("New topic.")]])
    jarvis = Jarvis(client=fake, computer=h.cu, store=store)
    ask(jarvis, "Hi Jarvis", h)
    ask(jarvis, "Weather?", h)
    first = jarvis.conversation_id
    conv = store.get_conversation(first)
    assert [(m["role"], m["text"]) for m in conv["messages"]] == [
        ("user", "Hi Jarvis"), ("assistant", "Hello, Gill."), ("user", "Weather?"), ("assistant", "It's sunny.")]
    jarvis.reset()
    ask(jarvis, "Something else", h)
    assert jarvis.conversation_id != first and len(store.list_conversations()) == 2


def test_failed_exchange_is_not_saved_and_request_log_is_masked(tmp_path, store, monkeypatch):
    monkeypatch.setattr(agent, "REQUEST_LOG", tmp_path / "requests.jsonl")
    h = Harness(tmp_path)

    def boom(p):
        import anthropic
        import httpx
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))

    jarvis = Jarvis(client=FakeAnthropic([boom]), computer=h.cu, store=store)
    ask(jarvis, "my password is hunter22, log me in", h)
    assert store.list_conversations() == []
    logged = (tmp_path / "requests.jsonl").read_text()
    assert "hunter22" not in logged and "removed" in logged


def test_deleting_the_live_conversation_clears_jarvis_context(tmp_path, store):
    h = Harness(tmp_path)
    jarvis = Jarvis(client=FakeAnthropic([lambda p: [text("Noted.")]]), computer=h.cu, store=store)
    ask(jarvis, "Plan the surprise party", h)
    assert jarvis.messages
    jarvis.forget_conversation(jarvis.conversation_id + 1)  # some other conversation: no effect
    assert jarvis.messages
    jarvis.forget_conversation(jarvis.conversation_id)
    assert jarvis.messages == [] and jarvis.conversation_id is None


# ---------- tools ----------

def test_remember_tool_suggests_and_notifies_the_hud(tmp_path, store):
    h = Harness(tmp_path)
    fake = FakeAnthropic([
        lambda p: [tool_use("remember", {"note": "Gill prefers short answers", "project": "jarvis"})],
        lambda p: [text("I've suggested that; press Keep to save it.")],
    ])
    jarvis = Jarvis(client=fake, computer=h.cu, store=store)
    ask(jarvis, "Remember I like short answers", h)
    [fact] = store.list_facts()
    assert fact["status"] == "pending" and fact["project"] == "jarvis"
    assert {"type": "memory_changed"} in h.events
    result = fake.calls[1]["messages"][-1]["content"][0]
    assert "waiting for their approval" in result["content"]
    # Not approved yet, so not in the instructions of a new conversation.
    assert "short answers" not in agent.system_prompt(store)
    store.update_fact(fact["id"], approve=True)
    assert "Gill prefers short answers [jarvis]" in agent.system_prompt(store)


def test_remember_refuses_secrets_and_nothing_is_stored(tmp_path, store):
    h = Harness(tmp_path)
    fake = FakeAnthropic([
        lambda p: [tool_use("remember", {"note": "Gill's bank PIN: 4821"})],
        lambda p: [text("I can't store that.")],
    ])
    jarvis = Jarvis(client=fake, computer=h.cu, store=store)
    ask(jarvis, "remember my bank PIN: 4821", h)
    result = fake.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "never stores passwords" in result["content"]
    assert store.list_facts() == []
    assert {"type": "memory_changed"} not in h.events
    # The request itself is kept in history, with the PIN masked.
    assert "4821" not in json.dumps(store.export())


def test_recall_and_search_history_tools(store):
    store.add_fact("Gill likes tea")
    store.add_fact("Gill might like coffee", source="assistant")
    out = tools.run_tool("recall", {})
    assert "Gill likes tea" in out and "coffee (waiting for the user's approval)" in out
    assert "tea" not in tools.run_tool("recall", {"query": "coffee"})
    assert "Nothing in past conversations" in tools.run_tool("search_history", {"query": "lisbon"})
    store.add_exchange(None, "Book Lisbon", "Booked the Lisbon hotel.")
    out = tools.run_tool("search_history", {"query": "lisbon", "limit": 5})
    assert "Booked the Lisbon hotel" in out and "user] Book Lisbon" in out
    assert tools.validate("search_history", {"query": "x", "limit": "5"}) == "Field 'limit' should be a integer."


# ---------- HTTP routes ----------

@pytest.fixture
def client(tmp_path, store, monkeypatch):
    h = Harness(tmp_path)
    monkeypatch.setattr(server, "jarvis", Jarvis(client=FakeAnthropic([]), computer=h.cu, store=store))
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


def test_api_review_edit_delete_facts(client, store):
    pending = store.add_fact("Gill likes tea", source="assistant")
    facts = client.get("/api/memory/facts", params={"status": "pending"}).json()["facts"]
    assert [f["id"] for f in facts] == [pending["id"]]
    assert client.patch(f"/api/memory/facts/{pending['id']}", json={"status": "approved"}).json()["status"] == "approved"
    added = client.post("/api/memory/facts", json={"text": "Gill lives in Lisbon", "project": "home"}).json()
    assert added["status"] == "approved" and added["source"] == "user"
    r = client.post("/api/memory/facts", json={"text": "my api key = sk-ant-api03-abcdefghijklmnop"})
    assert r.status_code == 422 and "never stores" in r.json()["detail"]
    r = client.patch(f"/api/memory/facts/{added['id']}", json={"text": "password: hunter22"})
    assert r.status_code == 422
    assert client.get("/api/memory/facts", params={"q": "lisbon"}).json()["facts"][0]["text"] == "Gill lives in Lisbon"
    assert client.delete(f"/api/memory/facts/{added['id']}").status_code == 200
    assert client.delete(f"/api/memory/facts/{added['id']}").status_code == 404
    assert client.patch("/api/memory/facts/9999", json={"status": "approved"}).status_code == 404
    info = client.get("/api/memory/info").json()
    assert info["facts"] == {"approved": 1, "pending": 0} and info["path"].endswith("jarvis.db")


def test_api_history_search_delete_and_export(client, store):
    cid = store.add_exchange(None, "Book Lisbon", "Booked the Lisbon hotel.")
    server.jarvis.conversation_id = cid
    server.jarvis.messages = [{"role": "user", "content": "Book Lisbon"}]
    assert client.get("/api/memory/conversations").json()["conversations"][0]["id"] == cid
    matches = client.get("/api/memory/conversations", params={"q": "hotel"}).json()["matches"]
    assert matches[0]["conversation_id"] == cid
    assert len(client.get(f"/api/memory/conversations/{cid}").json()["messages"]) == 2
    export = client.get("/api/memory/export")
    assert "attachment" in export.headers["content-disposition"]
    assert export.json()["conversations"][0]["messages"][1]["text"] == "Booked the Lisbon hotel."
    assert client.delete(f"/api/memory/conversations/{cid}").status_code == 200
    # Deleting the live conversation also clears what Jarvis was holding in mind.
    assert server.jarvis.messages == [] and server.jarvis.conversation_id is None
    assert client.get(f"/api/memory/conversations/{cid}").status_code == 404


def test_api_refuses_other_sites_and_rebinding(store):
    c = TestClient(server.app, base_url="http://127.0.0.1:8765")
    assert c.get("/api/memory/facts", headers={"origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/memory/facts", json={"text": "x"}, headers={"origin": "https://evil.example"}).status_code == 403
    # A page on evil.example resolved to 127.0.0.1 sends its own Host header.
    rebind = TestClient(server.app, base_url="http://evil.example:8765")
    assert rebind.get("/api/memory/export").status_code == 403
    assert c.get("/api/memory/facts", headers={"origin": "http://localhost:5173"}).status_code == 200
