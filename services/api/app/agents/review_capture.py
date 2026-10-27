"""What the reviewer needs to know about how a pack was made: the policy text the agents retrieved, what each
subagent returned, and every tool call. `_run_turn` fills a `RunCapture` while the team works."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

MAX_INPUT_CHARS = 200


@dataclass
class ToolCall:
    agent: str                  # orchestrator, or the subagent that made the call
    tool: str
    input: str                  # JSON, truncated
    is_error: bool = False
    result_chars: int = 0


@dataclass
class RunCapture:
    retrieved: dict[str, str] = field(default_factory=dict)    # policy sections the agents received, by source
    subagents: dict[str, dict] = field(default_factory=dict)   # latest parsed JSON reply per subagent
    unparsed: dict[str, str] = field(default_factory=dict)     # replies that were not a JSON object (start of text)
    runs: dict[str, int] = field(default_factory=dict)         # how many times each subagent returned
    tool_calls: list[ToolCall] = field(default_factory=list)

    def record_subagent(self, name: str, text: str) -> None:
        self.runs[name] = self.runs.get(name, 0) + 1
        parsed = parse_json_object(text)
        if parsed is None:
            self.unparsed[name] = text[:200]
            self.subagents.pop(name, None)
        else:
            self.subagents[name] = parsed
            self.unparsed.pop(name, None)

    def record_call(self, agent: str, tool: str, tool_input: object) -> ToolCall:
        call = ToolCall(agent, tool, json.dumps(tool_input, ensure_ascii=False, default=str)[:MAX_INPUT_CHARS])
        self.tool_calls.append(call)
        return call


def flatten_content(content: object) -> str:
    """A tool result's content as text (it is a string or a list of text blocks)."""
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return content if isinstance(content, str) else ""


def parse_json_object(text: str) -> dict | None:
    """The JSON object in a subagent's reply, which is meant to be bare JSON but may sit in a code fence or prose."""
    text = text.strip()
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        candidates.append(fenced.group(1).strip())
    if "{" in text and "}" in text:
        candidates.append(text[text.index("{"):text.rindex("}") + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None
