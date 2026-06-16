"""In-memory conversation state. No database for MVP."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ConversationState:
    conversation_id: str
    # Full message history in Anthropic format: [{"role": "user"|"assistant", "content": str}]
    messages: list[dict] = field(default_factory=list)

    # Pipeline determined by orchestrator
    pipeline: list[str] = field(default_factory=list)  # e.g. ["survey_bot", "translation_bot"]
    current_step: int = 0  # Index into pipeline

    # Outputs from each completed skill (skill_name → output text)
    skill_outputs: dict[str, str] = field(default_factory=dict)

    # Phase of current skill execution
    phase: str = "new"

    # Human-readable title (first user message, truncated)
    title: str = ""

    # Short summary of what was accomplished (for memory injection)
    summary: str = ""

    created_at: datetime = field(default_factory=datetime.utcnow)
    last_active: datetime = field(default_factory=datetime.utcnow)

    def current_skill_name(self) -> str | None:
        if not self.pipeline or self.current_step >= len(self.pipeline):
            return None
        return self.pipeline[self.current_step]

    def advance_pipeline(self) -> bool:
        """Move to next skill. Returns True if there's a next skill."""
        self.current_step += 1
        return self.current_step < len(self.pipeline)

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        self.last_active = datetime.utcnow()
        # Auto-set title from the first user message
        if role == "user" and not self.title:
            self.title = content[:80].replace("\n", " ")


class ConversationStore:
    def __init__(self) -> None:
        self._store: dict[str, ConversationState] = {}

    def get_or_create(self, conversation_id: str) -> ConversationState:
        if conversation_id not in self._store:
            self._store[conversation_id] = ConversationState(
                conversation_id=conversation_id
            )
        return self._store[conversation_id]

    def get(self, conversation_id: str) -> ConversationState | None:
        return self._store.get(conversation_id)

    def delete(self, conversation_id: str) -> bool:
        if conversation_id in self._store:
            del self._store[conversation_id]
            return True
        return False

    def list_all(self) -> list[ConversationState]:
        """Return all conversations sorted by most recently active."""
        return sorted(
            self._store.values(),
            key=lambda s: s.last_active,
            reverse=True,
        )

    def recent_summaries(self, exclude_id: str, limit: int = 4) -> list[dict]:
        """Return brief summaries of recent conversations for memory injection."""
        results = []
        for state in self.list_all():
            if state.conversation_id == exclude_id:
                continue
            if not state.title:
                continue
            results.append({
                "title": state.title,
                "summary": state.summary or _extract_summary(state),
            })
            if len(results) >= limit:
                break
        return results


def _extract_summary(state: ConversationState) -> str:
    """Best-effort summary from skill outputs or last assistant message."""
    if state.skill_outputs:
        parts = []
        for skill_name, output in state.skill_outputs.items():
            display = skill_name.replace("_", " ").title()
            parts.append(f"{display}: {output[:120]}…")
        return " | ".join(parts)
    # Fall back to last assistant message
    for msg in reversed(state.messages):
        if msg["role"] == "assistant" and msg["content"].strip():
            return msg["content"][:150] + "…"
    return ""


store = ConversationStore()
