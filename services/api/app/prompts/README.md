# Prompts

Keep every prompt in a file here instead of inline strings, so you can version, diff and tune them (Day 6: prompt optimisation needs a measurable before and after).

Suggested layout:

```
prompts/
  orchestrator.md        system prompt for the orchestrator / single agent
  registry_agent.md      one file per specialist
  news_agent.md
  financials_agent.md
  reviewer.md            the skeptical reviewer (Day 5)
  CHANGELOG.md           change, hypothesis, before score, after score
```

Load them with:

```python
from pathlib import Path
PROMPTS = Path(__file__).parent / "prompts"
system_prompt = (PROMPTS / "orchestrator.md").read_text()
```

Tips from the courses: be clear and direct, put data inside XML tags, give an example of a good pack, and state the three rules (no source, no claim; flag, don't judge; the AI recommends, the human decides).
