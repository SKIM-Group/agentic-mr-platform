"""Chat endpoint with SSE streaming and multi-skill pipeline orchestration."""

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import verify_api_key
from app.conversations.store import ConversationState, store
from app.core.claude import claude
from app.skills.models import Skill
from app.skills.registry import registry
from app.tools.executor import execute_tool

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Tool definitions injected into skill conversations ──────────────────────

OUTPUTS_DIR = Path(__file__).resolve().parents[4] / "outputs"

# Custom tools handled by execute_tool()
CUSTOM_TOOLS = [
    {
        "name": "save_file",
        "description": "Save plain text or markdown content to a .txt or .md file on disk.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Filename, e.g. output.txt"},
                "content": {"type": "string", "description": "Full text content to write."},
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "create_word_doc",
        "description": "Create a Microsoft Word (.docx) document from markdown-formatted content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Filename without extension, e.g. report"},
                "content": {"type": "string", "description": "Markdown content for the document."},
                "title": {"type": "string", "description": "Optional document title."},
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "create_excel_file",
        "description": "Create an Excel (.xlsx) spreadsheet. Data can be a JSON array of objects or CSV text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Filename without extension, e.g. data"},
                "data": {"type": "string", "description": "JSON array of row objects OR CSV text with header row."},
                "sheet_name": {"type": "string", "description": "Sheet tab name (default: Sheet1)."},
            },
            "required": ["filename", "data"],
        },
    },
    {
        "name": "skill_complete",
        "description": (
            "Call this tool ONLY when you have gathered ALL required information "
            "and have fully completed the task for this skill. "
            "Do NOT call this while still gathering information or asking questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "output": {
                    "type": "string",
                    "description": "The complete, final output produced by this skill.",
                }
            },
            "required": ["output"],
        },
    }
]

# Native Anthropic-hosted tools (no local handling needed)
NATIVE_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "code_execution_20260120", "name": "code_execution"},
]

# Full tool set passed to every skill execution
SKILL_TOOLS = NATIVE_TOOLS + CUSTOM_TOOLS


def _build_skill_system_prompt(skill: Skill, prior_outputs: dict[str, str]) -> str:
    """Compose the system prompt for a skill execution, injecting prior pipeline outputs."""
    prompt_parts = [skill.system_prompt]

    if prior_outputs:
        prompt_parts.append("\n\n---\n## Context from previous steps in this pipeline\n")
        for skill_name, output in prior_outputs.items():
            display = skill_name.replace("_", " ").title()
            prompt_parts.append(f"### Output from {display}:\n{output}")

    required = skill.manifest.required_fields
    if required:
        prompt_parts.append("\n\n---\n## Required Information\n")
        prompt_parts.append(
            "You MUST gather the following before completing the task. "
            "Ask for one piece of missing information at a time. "
            "When you have everything, call the `skill_complete` tool with your final output.\n"
        )
        for f in required:
            auto_filled = skill_name_in_prior(f.name, prior_outputs)
            status = " *(can be inferred from prior context)*" if auto_filled else " *(ask user)*"
            prompt_parts.append(f"- **{f.name}**: {f.description}{status}")

    optional = skill.manifest.optional_fields
    if optional:
        prompt_parts.append("\n\n## Optional Information\n")
        for f in optional:
            prompt_parts.append(f"- **{f.name}**: {f.description}")

    return "\n".join(prompt_parts)


def skill_name_in_prior(field_name: str, prior_outputs: dict[str, str]) -> bool:
    """Heuristic: check if a field value might be inferrable from prior outputs."""
    if not prior_outputs:
        return False
    # "content" field can usually be filled from prior output
    return field_name.lower() in {"content", "text", "survey", "document", "input"}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _build_memory_context(current_conversation_id: str) -> str:
    """Build a memory string from recent past conversations."""
    summaries = store.recent_summaries(exclude_id=current_conversation_id, limit=4)
    if not summaries:
        return ""
    parts = ["\n\n---\n## Memory from past conversations\n"]
    for s in summaries:
        parts.append(f"- **{s['title']}**")
        if s["summary"]:
            parts.append(f"  → {s['summary']}")
    return "\n".join(parts)


async def _run_pipeline(
    state: ConversationState,
    user_message: str,
) -> AsyncIterator[str]:
    """Core pipeline orchestration — yields SSE strings."""

    # ── Phase 1: Route ───────────────────────────────────────────────────────
    if state.phase == "new":
        state.phase = "routing"
        state.add_message("user", user_message)

        yield _sse({"type": "status", "text": "Analyzing your request..."})

        skills_summary = registry.skills_summary()
        if skills_summary and "No skills" not in skills_summary:
            pipeline = await claude.plan_pipeline(user_message, skills_summary)
            # Filter to valid skill names only
            pipeline = [p for p in pipeline if registry.get_by_name(p)]
        else:
            pipeline = []

        if pipeline:
            state.pipeline = pipeline
            state.phase = "skill_active"
            yield _sse({
                "type": "pipeline_planned",
                "pipeline": [
                    {
                        "name": s,
                        "display_name": (
                            registry.get_by_name(s).manifest.display_name
                            if registry.get_by_name(s)
                            else s
                        ),
                    }
                    for s in pipeline
                ],
            })
        else:
            # No skill matched — general conversation
            state.phase = "general"
            yield _sse({"type": "status", "text": "Responding..."})
            memory_context = _build_memory_context(state.conversation_id)
            async for chunk in claude.stream_skill_turn(
                system_prompt=(
                    "You are a helpful AI assistant for market research. "
                    "Answer the user's question helpfully and concisely."
                    + memory_context
                ),
                messages=state.messages,
            ):
                if chunk["type"] == "token":
                    yield _sse({"type": "token", "text": chunk["text"]})
            state.phase = "new"  # Reset for next message
            yield _sse({"type": "done"})
            return

    # ── Phase 2: Execute skills in pipeline order ────────────────────────────
    if state.phase in ("skill_active", "routing"):
        state.phase = "skill_active"

    while state.current_step < len(state.pipeline):
        skill_name = state.pipeline[state.current_step]
        skill = registry.get_by_name(skill_name)

        if not skill:
            yield _sse({"type": "error", "text": f"Skill '{skill_name}' not found."})
            break

        yield _sse({
            "type": "skill_start",
            "skill_name": skill_name,
            "display_name": skill.manifest.display_name,
            "step": state.current_step,
            "total": len(state.pipeline),
        })

        # Build system prompt with prior outputs + memory injected
        memory_context = _build_memory_context(state.conversation_id)
        system_prompt = _build_skill_system_prompt(skill, state.skill_outputs) + memory_context

        # If this isn't the first message for this skill, add the user message now
        # (first message already added during routing phase)
        if state.current_step > 0 or state.phase != "new":
            # User message already in state.messages for step 0
            # For step > 0 the transition message was added automatically
            pass

        # Stream the conversation turn — claude.py handles the custom tool loop
        skill_done = False
        tool_call_buffer: dict | None = None

        async for chunk in claude.stream_skill_turn(
            system_prompt=system_prompt,
            messages=state.messages,
            tools=SKILL_TOOLS,
            tool_executor=execute_tool,
        ):
            if chunk["type"] == "token":
                yield _sse({"type": "token", "text": chunk["text"]})
            elif chunk["type"] == "tool_event":
                # A custom tool was executed — stream the event to the client
                logger.info("Tool executed: %s", chunk["event"].get("tool") or chunk["event"].get("filename"))
                yield _sse(chunk["event"])
            elif chunk["type"] == "tool_call" and chunk["name"] == "skill_complete":
                tool_call_buffer = chunk
                skill_done = True

        # Reconstruct assistant message from streamed content for history
        # We'll add a placeholder; real apps would capture full text
        if skill_done and tool_call_buffer:
            output = tool_call_buffer["input"].get("output", "")
            state.skill_outputs[skill_name] = output
            state.add_message(
                "assistant",
                f"I have completed the {skill.manifest.display_name} task. Here is the output:\n\n{output}",
            )

            yield _sse({
                "type": "skill_complete",
                "skill_name": skill_name,
                "display_name": skill.manifest.display_name,
            })

            has_next = state.advance_pipeline()
            if has_next:
                next_skill_name = state.pipeline[state.current_step]
                next_skill = registry.get_by_name(next_skill_name)
                next_display = next_skill.manifest.display_name if next_skill else next_skill_name
                yield _sse({
                    "type": "pipeline_transition",
                    "from_skill": skill_name,
                    "to_skill": next_skill_name,
                    "to_display": next_display,
                })
                # Auto-inject transition message so Claude knows context
                state.add_message(
                    "user",
                    (
                        f"The {skill.manifest.display_name} step is complete. "
                        f"Now please proceed with the {next_display} step using the output above."
                    ),
                )
                # Continue loop to next skill
            else:
                # All skills complete — save summary for memory
                if state.skill_outputs:
                    from app.conversations.store import _extract_summary  # noqa: PLC0415
                    state.summary = _extract_summary(state)
                state.phase = "new"
                state.pipeline = []
                state.current_step = 0
                yield _sse({"type": "done"})
                return
        else:
            # Skill is still gathering info — capture the text response
            # Next user message will continue the conversation
            state.add_message("assistant", "[gathering information]")
            yield _sse({"type": "done"})
            return

    yield _sse({"type": "done"})


# ── Request / response models ────────────────────────────────────────────────

class ChatRequest(BaseModel):
    conversation_id: str
    message: str


# ── Route ────────────────────────────────────────────────────────────────────

@router.post("/chat")
async def chat(
    req: ChatRequest,
    _: None = Depends(verify_api_key),
) -> StreamingResponse:
    state = store.get_or_create(req.conversation_id)

    # If mid-skill (gathering info), add the user reply to history
    if state.phase == "skill_active":
        state.add_message("user", req.message)

    async def generate() -> AsyncIterator[bytes]:
        try:
            async for sse_line in _run_pipeline(state, req.message):
                yield sse_line.encode()
        except Exception as exc:
            logger.exception("Pipeline error: %s", exc)
            yield _sse({"type": "error", "text": str(exc)}).encode()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/conversations")
async def list_conversations(
    _: None = Depends(verify_api_key),
) -> dict:
    all_states = store.list_all()
    return {
        "conversations": [
            {
                "conversation_id": s.conversation_id,
                "title": s.title or "New conversation",
                "summary": s.summary,
                "message_count": len(s.messages),
                "phase": s.phase,
                "last_active": s.last_active.isoformat(),
                "created_at": s.created_at.isoformat(),
            }
            for s in all_states
            if s.title  # only show conversations that have at least one message
        ]
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    _: None = Depends(verify_api_key),
) -> dict:
    state = store.get(conversation_id)
    if not state:
        return {"conversation_id": conversation_id, "messages": [], "pipeline": [], "phase": "new"}
    return {
        "conversation_id": state.conversation_id,
        "messages": state.messages,
        "pipeline": state.pipeline,
        "current_step": state.current_step,
        "phase": state.phase,
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    _: None = Depends(verify_api_key),
) -> dict:
    deleted = store.delete(conversation_id)
    return {"deleted": deleted, "conversation_id": conversation_id}
