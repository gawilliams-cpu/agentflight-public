"""A scripted stand-in for the Claude API, so loop tests run free and offline."""

from types import SimpleNamespace


def tool_use(name, arguments, id=None):
    return SimpleNamespace(type="tool_use", name=name, input=arguments, id=id or f"call-{name}")


def text(words):
    return SimpleNamespace(type="text", text=words)


def response(*blocks, stop_reason=None):
    if stop_reason is None:
        stop_reason = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    usage = SimpleNamespace(input_tokens=100, cache_creation_input_tokens=0,
                            cache_read_input_tokens=1000, output_tokens=50)
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason, usage=usage,
                           model="claude-sonnet-5-5")


class FakeClaude:
    """Returns the scripted responses in order and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        # Snapshot the messages: the loop keeps appending to the same list.
        self.requests.append({**request, "messages": list(request["messages"])})
        return self.responses.pop(0)
