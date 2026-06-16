# Skills Platform — Market Research AI

An AI platform where analysts write **skills as Markdown files**, and Claude automatically routes user requests to the right skill — or chains multiple skills together in a pipeline.

## Architecture

```
User message → Orchestrator (Claude Haiku) → Determines pipeline
                                                     ↓
                                       [survey_bot → translation_bot]
                                                     ↓
                                    Claude Opus executes each skill
                                    (asks for missing info, then produces output)
```

## Quick Start

### 1. Backend

```bash
cd backend

# Create venv and install deps
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install fastapi "uvicorn[standard]" anthropic python-frontmatter watchdog \
    pydantic pydantic-settings python-multipart python-dotenv aiofiles

# Configure env
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY

# Start
uvicorn app.main:app --reload --port 8000
```

### 2. Frontend

```bash
cd frontend

# Install deps
npm install

# Configure env (already set to dev defaults)
cp .env.local.example .env.local

# Start
npm run dev
```

Open http://localhost:3000

## Skills

Skills live in `backend/skills_data/`. Each is a `.md` file with YAML frontmatter:

```markdown
---
name: my_skill
display_name: My Skill
description: What this skill does and when to use it.
triggers: [keyword1, keyword2]
required_fields:
  - name: topic
    description: The topic to research
    prompt: "What topic should I analyse?"
---

## Role
You are a [role]...

## Instructions
...
When you have all the information, call the `skill_complete` tool.
```

**Hot-reload**: Drop a new `.md` file into `skills_data/` and it appears in the UI within seconds.

## Multi-Skill Pipelines

Request "design a survey about coffee and translate it to French" and the platform automatically:
1. Plans pipeline: `[survey_bot → translation_bot]`
2. Survey Bot gathers your objectives and target audience → produces survey
3. Translation Bot receives the survey as context → asks for target language → translates

The UI shows pipeline progress in real-time.

## Skill Management UI

- **View skills**: Left sidebar lists all loaded skills with descriptions and trigger keywords
- **Create skill**: Click `+` button → markdown editor with template
- **Edit skill**: Hover over skill card → edit icon
- **Upload skill**: Click upload icon → drag/drop a `.md` file
- **Delete skill**: Hover over skill card → trash icon

## API

```
GET    /api/skills              List all skills
GET    /api/skills/{name}       Get skill details + raw markdown
POST   /api/skills              Create skill (JSON: {name, content})
PUT    /api/skills/{name}       Update skill
DELETE /api/skills/{name}       Delete skill
POST   /api/skills/upload       Upload .md file

POST   /api/chat                Send message (SSE streaming)
GET    /api/conversations/{id}  Get conversation history
DELETE /api/conversations/{id}  Clear conversation

GET    /health                  Health check
```
