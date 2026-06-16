# Backend Architecture

> Skills-as-Markdown AI platform for market research, built on FastAPI + Anthropic SDK.

---

## Directory Structure

```
backend/
├── app/
│   ├── main.py                  # FastAPI entry point, lifespan (load skills + watcher)
│   ├── core/
│   │   ├── config.py            # Settings from .env (API keys, model names, skills dir)
│   │   └── claude.py            # Anthropic SDK wrapper — streaming + tool loop
│   ├── api/
│   │   ├── deps.py              # Bearer token auth dependency
│   │   └── routes/
│   │       ├── chat.py          # POST /api/chat — SSE pipeline orchestration
│   │       ├── skills.py        # CRUD /api/skills
│   │       ├── extract.py       # POST /api/extract — file → text
│   │       └── export.py        # POST /api/export — text → DOCX/XLSX/PDF/etc
│   ├── skills/
│   │   ├── models.py            # Skill, SkillManifest, FieldSpec (Pydantic)
│   │   ├── loader.py            # .md file parser (frontmatter + body)
│   │   └── registry.py          # Thread-safe skill registry with hot-reload
│   ├── conversations/
│   │   └── store.py             # In-memory conversation state
│   └── tools/
│       └── executor.py          # Tool handlers (web search, files, docs, code)
└── skills_data/
    ├── survey_bot.md
    ├── translation_bot.md
    ├── competitive_analysis.md
    └── save_file.md
```

---

## System Architecture

```
Client (SSE consumer)
        │
        │  POST /api/chat { conversation_id, message }
        ▼
┌───────────────────────────────────────────────────────┐
│  FastAPI  (/api/chat)                                 │
│  Verifies Bearer token → streams SSE response         │
└───────────────────────┬───────────────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────────────┐
│  Pipeline Orchestrator  (_run_pipeline)               │
│                                                       │
│  1. Load ConversationState from in-memory store       │
│  2. ROUTE  → plan_pipeline() with Haiku               │
│     "survey_bot" | "translation_bot" | [] (general)  │
│  3. EXECUTE skills in order                           │
│     ├─ Build system prompt (skill + prior outputs     │
│     │  + memory from past conversations)              │
│     ├─ stream_skill_turn() → tokens + tool events     │
│     ├─ Transition: inject context message             │
│     └─ All done: extract summary for memory           │
└──────────────┬─────────────────────────┬──────────────┘
               │                         │
               ▼                         ▼
┌──────────────────────┐   ┌─────────────────────────────┐
│  Claude (Opus 4.6)   │   │  Tool Executor              │
│                      │   │                             │
│  stream_skill_turn() │   │  Custom tools (local):      │
│  ┌────────────────┐  │   │  • save_file → outputs/     │
│  │  Stream loop   │  │   │  • create_word_doc (docx)   │
│  │  ─────────────│  │   │  • create_excel_file (xlsx) │
│  │  text_delta   │──┼──▶│  • read_file                │
│  │  tool_use ────┼──┼──▶│  • run_code (subprocess)    │
│  │  tool_result  │◀─┼───┤  • web_search (DuckDuckGo)  │
│  └────────────────┘  │   │                             │
│                      │   │  Native tools (Anthropic):  │
│  Routing (Haiku):    │   │  • web_search_20260209      │
│  plan_pipeline()     │   │  • code_execution_20260120  │
└──────────────────────┘   └─────────────────────────────┘
               │
               ▼
┌───────────────────────────────────────────────────────┐
│  Skills Registry                                      │
│                                                       │
│  .md files → Skill { manifest + system_prompt }      │
│  Watchdog monitors directory → hot-reload on change   │
└───────────────────────────────────────────────────────┘
               │
               ▼
┌───────────────────────────────────────────────────────┐
│  Conversation Store (in-memory)                       │
│                                                       │
│  { conversation_id → ConversationState }              │
│  • message history (Anthropic format)                 │
│  • pipeline state (steps, current_step, phase)        │
│  • skill_outputs (passed between skills)              │
│  • summaries (injected as memory in future turns)     │
└───────────────────────────────────────────────────────┘
```

---

## Request Flow (Step by Step)

```
1.  Client sends  POST /api/chat { conversation_id, message }

2.  API loads or creates ConversationState

3.  phase == "new":
      Routing model (Haiku) reads skills_summary() + user message
      → returns { pipeline: ["survey_bot", "translation_bot"] }
      → pipeline validated against registry
      → if empty: general conversation (no skills)

4.  phase == "skill_active":
      For each skill in pipeline:

      a. Emit:  { type: "skill_start", step, total, display_name }

      b. Build system prompt:
           skill.system_prompt
           + "Context from previous steps" (prior skill outputs)
           + "Memory from past conversations" (recent summaries)
           + required/optional field guidance

      c. Call claude.stream_skill_turn(system_prompt, messages, tools, tool_executor)

      d. Stream loop (inside claude.py):
           ┌─ Claude streams text → yield tokens to client
           ├─ Claude calls native tool (web_search / code_execution)
           │    → Anthropic executes it server-side, Claude sees result automatically
           ├─ Claude calls custom tool (save_file / create_word_doc / etc.)
           │    → execute_tool(name, inputs) runs locally
           │    → result appended as tool_result message
           │    → Claude re-called to continue (loop)
           └─ Claude calls skill_complete { output: "..." }
                → loop exits, output saved

      e. Emit:  { type: "skill_complete" }

      f. If more skills remain:
           Emit:  { type: "pipeline_transition" }
           Inject: "The X step is complete. Now proceed with Y."
           Continue to next skill

      g. Last skill done:
           Extract summary from skill_outputs → save for memory
           Reset state (phase = "new") for next message

5.  Emit:  { type: "done" }
```

---

## SSE Event Reference

| Event | When | Key Fields |
|-------|------|------------|
| `status` | Routing / general chat | `text` |
| `pipeline_planned` | Skills selected | `pipeline: [{name, display_name}]` |
| `skill_start` | Skill begins | `skill_name`, `display_name`, `step`, `total` |
| `token` | Streaming text | `text` |
| `tool_used` | Custom tool called | `tool`, `query`/`filename` |
| `file_saved` | File written to disk | `filename`, `path`, `size`, `format` |
| `skill_complete` | Skill finished | `skill_name`, `display_name` |
| `pipeline_transition` | Moving to next skill | `from_skill`, `to_skill`, `to_display` |
| `error` | Any error | `text` |
| `done` | Stream finished | — |

---

## Skill File Format

```markdown
---
name: survey_bot                          # internal ID
display_name: Survey Bot                  # shown to users
description: Designs market research...   # used for routing decisions
version: "1.0"
triggers:                                 # keywords that activate this skill
  - survey
  - questionnaire
required_fields:                          # Claude must collect these before completing
  - name: research_objective
    description: The business question to answer
    prompt: "What is the primary research objective?"
optional_fields:                          # Claude can use if provided
  - name: survey_length
    description: Number of questions
    prompt: "How long should the survey be?"
---

## Role
You are a senior market research survey designer...

## Core Principles
- Single-barrelled questions: one idea per question
...
```

Skills are hot-reloaded — editing a `.md` file takes effect immediately without restarting the server.

---

## Tool System

### Native Tools (Anthropic-hosted)
Declared in `NATIVE_TOOLS` list. Anthropic runs these server-side; no local execution needed.

| Tool type | What it does |
|-----------|-------------|
| `web_search_20260209` | Live web search with dynamic filtering |
| `code_execution_20260120` | Python sandbox (Anthropic's servers) |

### Custom Tools (Local)
Defined in `CUSTOM_TOOLS`, executed by `executor.py`.

| Tool name | What it does | Output |
|-----------|-------------|--------|
| `save_file` | Write text/markdown to disk | `outputs/<filename>` |
| `create_word_doc` | Markdown → .docx via python-docx | `outputs/<filename>.docx` |
| `create_excel_file` | JSON/CSV → .xlsx via openpyxl | `outputs/<filename>.xlsx` |
| `read_file` | Read from outputs/ directory | file contents string |
| `run_code` | Run Python in subprocess (15s limit) | stdout/stderr |
| `web_search` | DuckDuckGo search (fallback) | top-5 results |
| `skill_complete` | Signal skill finished | triggers pipeline advance |

### Tool Loop (claude.py)
```
stream_skill_turn():
  while True:
    stream one API call
    collect tool_use blocks from stream

    if skill_complete in tools → yield it, return
    if no custom tool_use blocks → return (done)

    for each custom tool:
      execute via tool_executor(name, inputs)
      yield tool_event to client

    append assistant message (with tool_use blocks) to history
    append tool_result messages to history
    loop → re-call Claude with updated history
```

---

## Data Models

### ConversationState
```python
conversation_id: str
messages: list[dict]           # [{"role": "user"|"assistant", "content": str}]
pipeline: list[str]            # ["survey_bot", "translation_bot"]
current_step: int              # index into pipeline
skill_outputs: dict[str, str]  # {"survey_bot": "full output text"}
phase: str                     # "new" | "routing" | "skill_active" | "general"
title: str                     # first user message (truncated to 80 chars)
summary: str                   # extracted for memory injection
```

### Skill
```python
manifest:
  name: str                    # "survey_bot"
  display_name: str            # "Survey Bot"
  description: str
  triggers: list[str]          # routing keywords
  required_fields: list[FieldSpec]
  optional_fields: list[FieldSpec]
system_prompt: str             # markdown body → Claude's instruction
file_path: str
loaded_at: datetime
```

---

## Models Used

| Purpose | Model | Why |
|---------|-------|-----|
| Routing | `claude-haiku-4-5` | Fast, cheap — just needs to return skill names |
| Skill execution | `claude-opus-4-6` | Full reasoning, tool use, long outputs |

---

## Configuration (.env)

```env
ANTHROPIC_API_KEY=sk-ant-...
APP_API_KEY=dev-key           # Bearer token for API auth
SKILLS_DIR=./skills_data      # Where .md skill files live
CHAT_MODEL=claude-opus-4-6
ROUTING_MODEL=claude-haiku-4-5
ALLOWED_ORIGINS=http://localhost:3000
```

---

## Known Limitations (MVP)

| Limitation | Impact | Future fix |
|-----------|--------|-----------|
| In-memory conversation store | Lost on restart | PostgreSQL / Redis |
| Single-process only | No horizontal scaling | Shared state layer |
| No auth per user | All conversations shared | User auth + per-user store |
| Outputs written to local disk | Not accessible in cloud | S3 / object storage |
| No streaming for tool results | Tool output appears after loop | Streaming tool events |
