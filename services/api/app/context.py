"""What your pipeline receives: the request, the human's feedback (if any) and a way to report progress."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from kyc_common import KycPack

from .settings import Settings, settings as default_settings


@dataclass
class PipelineContext:
    run_id: str
    company_name: str
    country: str                      # "UK", "PT" or "BR"
    version: int = 1                  # 1 for the first pack; +1 each time the human sends it back
    feedback: str | None = None       # the reviewer's comments when the pack was sent back
    previous_pack: KycPack | None = None
    settings: Settings = field(default_factory=lambda: default_settings)
    _emit: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    async def emit(self, step: str, message: str, **data: Any) -> None:
        """Report progress to the web app (shown live while the run is in flight).

        Keep personal data out of progress messages: they are stored and shown to anyone using the app.
        """
        if self._emit:
            await self._emit({"type": "progress", "step": step, "message": message, "data": data or None})
