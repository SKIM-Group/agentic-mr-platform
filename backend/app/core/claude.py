"""Anthropic SDK wrapper — streaming + tool loop."""

import json
import logging
from collections.abc import AsyncIterator, Callable

import anthropic

from app.core.config import settings

logger = logging.getLogger(__name__)

# Tool types that run server-side — Anthropic handles their execution automatically.
# They appear as `server_tool_use` blocks, NOT `tool_use`, so we never see them
# as pending tool calls that need a tool_result.
NATIVE_TOOL_TYPES = {"web_search_20260209", "code_execution_20260120"}


class ClaudeClient:
    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def plan_pipeline(self, user_message: str, skills_summary: str) -> list[str]:
        """Use a fast model to decide which skills to invoke, in order."""
        prompt = f"""You are a market research AI orchestrator. Determine which skills to use (in order) for this request.

Available skills:
{skills_summary}

User request: "{user_message}"

Rules:
- Return a JSON object: {{"pipeline": ["skill_name"], "reasoning": "..."}}
- pipeline can have 1-3 skills in execution order
- Return empty pipeline [] if no skill matches (general conversation)
- For requests like "design a survey and translate it", return ["survey_bot", "translation_bot"]
- Only include skills that are genuinely needed

Respond with ONLY the JSON object, no other text."""

        response = await self._client.messages.create(
            model=settings.routing_model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in response.content if b.type == "text"), "{}")
        try:
            clean = text.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[-1]
                clean = clean.rsplit("```", 1)[0].strip()
            return json.loads(clean).get("pipeline", [])
        except (json.JSONDecodeError, AttributeError):
            return []

    async def stream_skill_turn(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_executor: Callable[[str, dict], tuple[str, dict]] | None = None,
    ) -> AsyncIterator[dict]:
        """Stream a skill turn with a proper tool loop for custom tools.

        For native server-side tools (web_search, code_execution) Anthropic runs
        the tool loop automatically — we just stream the result.

        For custom tools (save_file, create_word_doc, etc.) we:
          1. Execute the tool via tool_executor(name, inputs) → (result_text, sse_event)
          2. Append the assistant + tool_result messages
          3. Re-call Claude so it can continue

        Yields dicts:
          {"type": "token",      "text": ...}
          {"type": "tool_event", "event": {...}}   ← SSE event from a custom tool call
          {"type": "tool_call",  "name": "skill_complete", "input": {...}}
        """
        current_messages = list(messages)

        while True:
            # ── single streaming API call ──────────────────────────────────
            response_blocks: list = []      # full content blocks for history
            pending_tools: list[dict] = []  # custom tool_use blocks seen this turn

            current_tool: dict | None = None
            current_json = ""

            kwargs: dict = {
                "model": settings.chat_model,
                "max_tokens": 8096,
                "system": system_prompt,
                "messages": current_messages,
            }
            if tools:
                kwargs["tools"] = tools

            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type == "content_block_start":
                        blk = event.content_block
                        if blk.type == "tool_use":
                            current_tool = {"id": blk.id, "name": blk.name}
                            current_json = ""
                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield {"type": "token", "text": delta.text}
                        elif delta.type == "input_json_delta" and delta.partial_json:
                            current_json += delta.partial_json
                    elif event.type == "content_block_stop":
                        if current_tool:
                            try:
                                tool_input = json.loads(current_json) if current_json else {}
                            except json.JSONDecodeError:
                                tool_input = {}
                            current_tool["input"] = tool_input
                            pending_tools.append(current_tool)
                            current_tool = None
                            current_json = ""

                # Capture full response content for history
                final = await stream.get_final_message()
                response_blocks = list(final.content)
                stop_reason = final.stop_reason

            # ── skill_complete signals the skill is done ───────────────────
            sc = next((t for t in pending_tools if t["name"] == "skill_complete"), None)
            if sc:
                yield {"type": "tool_call", "name": "skill_complete", "input": sc["input"]}
                return

            # ── custom tools: execute and loop back ────────────────────────
            custom = [t for t in pending_tools if t["name"] not in NATIVE_TOOL_TYPES]
            if not custom:
                return  # end_turn with no custom tools — done

            if tool_executor is None:
                logger.warning("Custom tools called but no tool_executor provided: %s",
                               [t["name"] for t in custom])
                return

            # Append assistant turn (including tool_use blocks) to history
            current_messages.append({
                "role": "assistant",
                "content": [_block_to_dict(b) for b in response_blocks],
            })

            # Execute each custom tool and collect tool_result blocks
            tool_results = []
            for t in custom:
                result_text, sse_event = tool_executor(t["name"], t["input"])
                yield {"type": "tool_event", "event": sse_event}
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": t["id"],
                    "content": result_text,
                })

            # Append tool results so Claude can continue
            current_messages.append({"role": "user", "content": tool_results})

            # Handle pause_turn (server-side tool loop hit its limit — just re-call)
            if stop_reason == "pause_turn":
                continue

    async def simple_chat(self, system_prompt: str, messages: list[dict]) -> str:
        response = await self._client.messages.create(
            model=settings.chat_model,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
        )
        return next((b.text for b in response.content if b.type == "text"), "")


def _block_to_dict(block: object) -> dict:
    """Convert an Anthropic content block to the minimal dict the API accepts."""
    t = getattr(block, "type", None)
    if t == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if t == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "input": getattr(block, "input", {}),
        }
    if t == "thinking":
        return {"type": "thinking", "thinking": getattr(block, "thinking", "")}
    # Fallback: strip any extra fields model_dump might add
    if hasattr(block, "model_dump"):
        raw = block.model_dump()
        return {k: v for k, v in raw.items() if k in ("type", "id", "name", "input", "text", "thinking")}
    return {}


claude = ClaudeClient()
