"""A scripted stand-in for the Anthropic client, so the agent loop runs without the network."""

import re
from types import SimpleNamespace


def tool_use(name, input, id=None):
    tool_use.n = getattr(tool_use, "n", 0) + 1
    return SimpleNamespace(type="tool_use", id=id or f"toolu_{tool_use.n}", name=name, input=input)


def text(t):
    return SimpleNamespace(type="text", text=t)


def last_tool_result_text(messages) -> str:
    """All text from the most recent tool results, to pull out ids the way Claude would."""
    out = []
    for block in messages[-1]["content"]:
        content = block.get("content")
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            out += [c["text"] for c in content if c.get("type") == "text"]
    return "\n".join(out)


def find(pattern, messages):
    m = re.search(pattern, last_tool_result_text(messages))
    return m.group(1) if m else None


class _Stream:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for block in self.response.content:
                if block.type == "text":
                    yield SimpleNamespace(type="text", text=block.text)
        return gen()

    async def get_final_message(self):
        return self.response


class FakeAnthropic:
    """Each step is a function(params) -> list of content blocks. Records every request's params."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **params):
        self.calls.append({k: v for k, v in params.items() if k != "messages"} | {"messages": list(params["messages"])})
        content = self.steps.pop(0)(params)
        stop = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"
        return _Stream(SimpleNamespace(content=content, stop_reason=stop, model=params["model"],
                                       usage=SimpleNamespace(input_tokens=1, output_tokens=1)))
