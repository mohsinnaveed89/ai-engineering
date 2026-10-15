# Agent home

A working directory for Claude Agent SDK sessions. Point your agents here so they load the Skills in `.claude/skills/` and nothing else from the repo:

```python
from pathlib import Path
from claude_agent_sdk import ClaudeAgentOptions

AGENT_HOME = Path(__file__).parent / "agent_home"
options = ClaudeAgentOptions(
    cwd=AGENT_HOME,
    skills="all",                        # or a list, e.g. ["kyc-uk"]; the SDK enables the Skill tool for you
    allowed_tools=["mcp__kyc__*"],       # least privilege: only your own tools, no Bash/Write
    max_turns=30,
)
```

## Skills (Day 4)

One folder per Skill, each with a `SKILL.md` (start from `.claude/skills/_template/SKILL.md.example`). Good candidates: the KYC procedure for each country (`data/documents/policies/procedures_uk.md`, `procedimentos_pt.md`, `procedimentos_br.md`). Only the name and description are loaded up front; the body loads when the Skill is relevant.
