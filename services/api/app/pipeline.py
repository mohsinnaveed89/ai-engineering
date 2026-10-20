"""
=====================================================================
  THIS IS WHERE YOUR AGENTS GO.
=====================================================================

`run_pipeline()` is the single entry point the API calls for every run.
It receives a PipelineContext and must return a valid `KycPack`
(see packages/kyc_common/kyc_common/schema.py).

Today it calls a placeholder that builds a pack straight from the mock data,
with NO AI: it copies register fields, lists news without judging it, and
always recommends "investigate_further". Replace the body with your own
pipeline (Claude Agent SDK, specialist agents, reviewer loop, RAG, MCP...).

Useful things on `ctx`:
  ctx.company_name, ctx.country       what the analyst submitted
  ctx.feedback, ctx.previous_pack     set when the human sent the pack back
  await ctx.emit(step, message)       live progress in the web app
  ctx.settings                        URLs and keys from .env

The API validates what you return against the schema, checks every fact has
a source, stores it and asks a human to review it.
"""
import json
from pathlib import Path
import re
from typing import Any

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict, ValidationError

from kyc_common import Flag, KycPack, Source

from .context import PipelineContext
from .mockdata_client import MockDataClient
from .placeholder import build_placeholder_pack, fact

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "orchestrator.md").read_text()

# USD per million tokens (input, output), for ctx.record_metrics. Unknown models report cost 0.
PRICES = {"claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0), "claude-haiku-5-5": (0.10, 0.50)}


class CompanyIdentity(BaseModel):
    """What we ask Claude for. Values are copied from the registry record, or null when it does not state them."""
    model_config = ConfigDict(extra="forbid")

    legal_name: str | None
    registration_number: str | None
    status: str | None
    address: str | None


class RegistryRecord(BaseModel):
    """A registry record plus where it came from, so every fact taken from it can cite it."""
    data: dict[str, Any]
    reference: str  # URL the reviewer can open
    title: str


async def fetch_registry_record(ctx: PipelineContext) -> RegistryRecord | None:
    """Fetch the company's registry record from the mock data API (plain Python, no agent).

    UK and PT: search by name, take the best hit, fetch its record.
    BR: no name search, so take the CNPJ from the bank's onboarding request first.
    Returns None when nothing is found.
    """
    async with MockDataClient(ctx.settings.mockdata_url) as api:
        if ctx.country == "UK":
            hits = ((await api.get("/uk/search", q=ctx.company_name)) or {}).get("items", [])
            path, title = (f"/uk/company/{hits[0]['company_number']}", "UK register: company profile") if hits else (None, "")
        elif ctx.country == "PT":
            hits = ((await api.get("/pt/pesquisa", nome=ctx.company_name)) or {}).get("resultados", [])
            path, title = (f"/pt/entidade/{hits[0]['nipc']}", "Registo comercial (PT): certidão") if hits else (None, "")
        elif ctx.country == "BR":
            prospects = ((await api.get("/bank/prospects", name=ctx.company_name, country="BR")) or {}).get("items", [])
            # The declared CNPJ is formatted "12.345.678/0001-90"; the slashes break the URL path.
            digits = re.sub(r"\D", "", prospects[0]["declared_registration_number"]) if prospects else ""
            path, title = (f"/br/cnpj/{digits}", "Cadastro CNPJ (BR)") if digits else (None, "")
        else:
            path, title = None, ""
        if not path:
            return None
        data = await api.get(path)
        return RegistryRecord(data=data, reference=api.url(path), title=title) if data else None


async def extract_identity(ctx: PipelineContext, record: dict[str, Any]) -> CompanyIdentity | None:
    client = AsyncAnthropic(api_key=ctx.settings.anthropic_api_key)
    messages: list[dict[str, Any]] = [{
        "role": "user",
        "content": (
            f"<registry_record>{json.dumps(record, ensure_ascii=False)}</registry_record>\n"
            "Copy the legal name, registration number, status and address exactly as written "
            "in the record. If the address is split into parts, join them into one string with "
            "\", \" in the order they appear, skipping empty parts. "
            "Use null for anything the record does not state."
        ),
    }]

    for attempt in range(2):  # first try + one retry
        resp = await client.messages.create(
            model=ctx.settings.claude_model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            output_config={
                "effort": "low",  # copying four fields needs little reasoning
                "format": {"type": "json_schema", "schema": CompanyIdentity.model_json_schema()},
            },
            messages=messages,
        )
        price_in, price_out = PRICES.get(ctx.settings.claude_model, (0.0, 0.0))
        await ctx.record_metrics(
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            cost_usd=(resp.usage.input_tokens * price_in + resp.usage.output_tokens * price_out) / 1e6,
            model=ctx.settings.claude_model,
        )
        if resp.stop_reason == "refusal":
            await ctx.emit("identity", "Model declined the identity request")
            return None
        text = next((b.text for b in resp.content if b.type == "text"), None)
        try:
            if text is None:
                raise ValueError(f"response had no text block (stop_reason={resp.stop_reason})")
            return CompanyIdentity.model_validate_json(text)
        except (ValidationError, ValueError) as e:
            await ctx.emit("identity", f"Identity output invalid (attempt {attempt + 1}/2)")
            messages += [
                {"role": "assistant", "content": resp.content},
                {"role": "user", "content": f"Your answer was invalid: {e}\nReturn the corrected JSON only."},
            ]
    return None  # both attempts failed


async def run_pipeline(ctx: PipelineContext) -> KycPack:
    # Day 1: Claude extracts the identity fields from the registry record.
    # The placeholder still supplies officers, owners, news and risk until later days replace them.
    pack = await build_placeholder_pack(ctx)
    if not ctx.settings.anthropic_api_key:
        await ctx.emit("identity", "No ANTHROPIC_API_KEY set: keeping the placeholder's identity fields")
        return pack

    await ctx.emit("identity", "Fetching the registry record for identity extraction")
    record = await fetch_registry_record(ctx)
    if record is None:
        return pack  # the placeholder has already raised a missing_data flag

    # Cite the registry record; reuse the placeholder's source when it fetched the same URL.
    sid = next((s.id for s in pack.sources if s.reference == record.reference), None)
    if sid is None:
        sid = f"src-{len(pack.sources) + 1}"
        pack.sources.append(Source(id=sid, type="registry", title=record.title, reference=record.reference))

    await ctx.emit("identity", "Extracting legal name, registration number, status and address with Claude")
    identity = await extract_identity(ctx, record.data)
    if identity is None:
        pack.flags.append(Flag(
            type="missing_data", severity="medium", sources=[sid],
            message="Identity fields could not be extracted from the registry record after one retry; "
                    "the identity values shown come from the preliminary lookup and have not been verified "
                    "against the registry record.",
        ))
        return pack

    pack.company = pack.company.model_copy(update={
        "legal_name": fact(identity.legal_name, sid),
        "registration_number": fact(identity.registration_number, sid),
        "status": fact(identity.status, sid),
        "address": fact(identity.address, sid),
    })
    # A value the record does not state stays null, and we say so instead of guessing.
    for name in ("legal_name", "registration_number", "status", "address"):
        if getattr(identity, name) in (None, ""):
            pack.flags.append(Flag(
                type="missing_data", severity="low", sources=[sid],
                message=f"{name.replace('_', ' ').capitalize()} is not stated in the registry record.",
            ))
    pack.generated_by = f"day1-identity ({ctx.settings.claude_model})"
    return pack
