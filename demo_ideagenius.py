#!/usr/bin/env python3
"""Interactive demo for IdeaGenius (WetDryVac) and SurveyChatbot on AI4MR LangGraph.

Usage:
    # Use the ai4mr-agents venv (has httpx + dotenv):
    /mnt/c/Users/NeerajSujan/Documents/AI/AI4MR/ai4mr-agents/.venv/bin/python demo_ideagenius.py

    python demo_ideagenius.py                  # demo_IdeageniusWetDryVac (default)
    python demo_ideagenius.py --list           # list all survey_chatbot agents
    python demo_ideagenius.py --id <agent_id>  # use a specific assistant ID
    python demo_ideagenius.py --name Ideagenius_airfryer_en  # find by name

Authentication uses LANGSMITH_API_KEY from environment (or hardcoded fallback).
"""

import asyncio
import json
import os
import sys
import httpx
from dotenv import load_dotenv

load_dotenv()

LANGGRAPH_URL = "https://ai4mr-agents-5c6c15c13e8a563aa6474d36cb36184b.eu.langgraph.app"
IDEAGENIUS_WETDRYVAC_ID = "cf4e8a6c-f3fb-479f-9f3b-2ecfb7c960b3"

API_KEY = os.getenv("LANGSMITH_API_KEY", "")
if not API_KEY:
    print("Error: LANGSMITH_API_KEY environment variable not set.")
    sys.exit(1)

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "x-api-key": API_KEY,
}

# ANSI colors
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


async def list_agents(agent_type: str = "survey_chatbot") -> list[dict]:
    """List all assistants of a given graph type."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{LANGGRAPH_URL}/assistants/search",
            headers=HEADERS,
            json={"limit": 100},
        )
        resp.raise_for_status()
        all_agents = resp.json()
        return [a for a in all_agents if a.get("graph_id") == agent_type]


async def get_agent(assistant_id: str) -> dict:
    """Fetch a single assistant's config."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{LANGGRAPH_URL}/assistants/{assistant_id}",
            headers=HEADERS,
        )
        resp.raise_for_status()
        return resp.json()


async def create_thread() -> str:
    """Create a new conversation thread."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{LANGGRAPH_URL}/threads",
            headers=HEADERS,
            json={},
        )
        resp.raise_for_status()
        return resp.json()["thread_id"]


async def stream_run(
    thread_id: str,
    assistant_id: str,
    user_message: str,
    prompt_variables: dict | None = None,
) -> dict:
    """Send a message and stream the response. Returns the final structured output."""
    payload = {
        "assistant_id": assistant_id,
        "input": {"messages": [{"role": "user", "content": user_message}]},
        "config": {"configurable": {"prompt_variables": prompt_variables or {}}},
        "stream_mode": ["values"],
    }

    stream_headers = {**HEADERS, "Accept": "text/event-stream"}
    last_values = {}

    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST",
            f"{LANGGRAPH_URL}/threads/{thread_id}/runs/stream",
            headers=stream_headers,
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                    if isinstance(event, dict) and "messages" in event:
                        last_values = event
                except json.JSONDecodeError:
                    continue

    return last_values


def parse_assistant_response(state: dict) -> dict | None:
    """Extract the last assistant message content from state."""
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if msg.get("type") == "ai" or msg.get("role") == "assistant":
            content = msg.get("content", "")
            if isinstance(content, str):
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    return {"assistantResponse": content}
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        try:
                            return json.loads(block["text"])
                        except json.JSONDecodeError:
                            return {"assistantResponse": block["text"]}
    return None


def print_agent_response(parsed: dict | None, raw_state: dict) -> bool:
    """Display the agent's response. Returns True if conversation should end."""
    if not parsed:
        print(f"{DIM}[No structured response found]{RESET}")
        return False

    # Display the assistant's reply
    reply = parsed.get("assistantResponse") or parsed.get("assistant_response", "")
    print(f"\n{CYAN}{BOLD}IdeaGenius:{RESET} {reply}\n")

    # Show current idea specification if populated
    idea = parsed.get("updatedIdeaSpecification") or parsed.get("updated_idea_specification")
    if idea:
        print(f"{GREEN}  💡 Current Idea: {idea}{RESET}")

    # Show readiness status
    looks_ready = parsed.get("looks_ready", False)
    mark_incomplete = parsed.get("markIncomplete") or parsed.get("mark_incomplete", False)
    end_conversation = parsed.get("endConversation") or parsed.get("end_conversation", False)

    if looks_ready:
        print(f"{GREEN}  ✅ Idea looks complete!{RESET}")

    if mark_incomplete:
        end_reason = parsed.get("endReason") or parsed.get("end_reason", "")
        print(f"{YELLOW}  ⚠️  Marked incomplete: {end_reason}{RESET}")

    # Show needs if present
    needs = parsed.get("needs")
    if needs:
        print(f"{DIM}  🎯 Needs: {needs}{RESET}")

    return end_conversation or mark_incomplete


async def run_demo(assistant_id: str, agent_name: str) -> None:
    """Run an interactive demo session."""
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  AI4MR Demo: {agent_name}{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    print(f"{DIM}Agent ID: {assistant_id}{RESET}")

    # Fetch and display config summary
    agent = await get_agent(assistant_id)
    cfg = agent.get("config", {}).get("configurable", {})
    print(f"{DIM}Model: {cfg.get('model')} | Mode: {cfg.get('mode')} | Format: {cfg.get('response_format')}{RESET}\n")

    # Create thread
    thread_id = await create_thread()
    print(f"{DIM}Thread: {thread_id}{RESET}")
    print(f"\n{DIM}Type your messages below. Commands: /new (new thread), /quit (exit){RESET}\n")

    # Start with initial greeting
    print(f"{YELLOW}Starting conversation...{RESET}")
    state = await stream_run(thread_id, assistant_id, "I'm ready to start the survey")
    parsed = parse_assistant_response(state)
    should_end = print_agent_response(parsed, state)

    if should_end:
        print(f"\n{YELLOW}Conversation ended by agent.{RESET}")
        return

    # Interactive loop
    while True:
        try:
            user_input = input(f"{MAGENTA}{BOLD}You:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{YELLOW}Exiting demo.{RESET}")
            break

        if not user_input:
            continue

        if user_input == "/quit":
            print(f"\n{YELLOW}Goodbye!{RESET}")
            break

        if user_input == "/new":
            thread_id = await create_thread()
            print(f"\n{DIM}New thread: {thread_id}{RESET}")
            state = await stream_run(thread_id, assistant_id, "I'm ready to start the survey")
            parsed = parse_assistant_response(state)
            should_end = print_agent_response(parsed, state)
            if should_end:
                break
            continue

        print(f"{DIM}  Thinking...{RESET}", end="\r")
        state = await stream_run(thread_id, assistant_id, user_input)
        parsed = parse_assistant_response(state)
        should_end = print_agent_response(parsed, state)

        if should_end:
            print(f"\n{YELLOW}Conversation ended by agent.{RESET}")
            retry = input(f"\n{DIM}Start new session? (y/n): {RESET}").strip().lower()
            if retry == "y":
                await run_demo(assistant_id, agent_name)
            break


async def main() -> None:
    args = sys.argv[1:]

    if "--list" in args:
        print(f"\n{BOLD}Available survey_chatbot agents:{RESET}\n")
        agents = await list_agents("survey_chatbot")
        for a in agents:
            name = a.get("name", "N/A")
            aid = a.get("assistant_id", "")
            cfg = a.get("config", {}).get("configurable", {})
            model = cfg.get("model", "?")
            mode = cfg.get("mode", "?")
            fmt = cfg.get("response_format") or "-"
            print(f"  {GREEN}{name:<40}{RESET} {DIM}{aid[:8]}... model={model} mode={mode} format={fmt}{RESET}")
        return

    if "--id" in args:
        idx = args.index("--id")
        if idx + 1 >= len(args):
            print("Error: --id requires an assistant ID argument")
            sys.exit(1)
        assistant_id = args[idx + 1]
        agent = await get_agent(assistant_id)
        agent_name = agent.get("name", assistant_id)

    elif "--name" in args:
        idx = args.index("--name")
        if idx + 1 >= len(args):
            print("Error: --name requires a name argument")
            sys.exit(1)
        target_name = args[idx + 1]
        agents = await list_agents("survey_chatbot")
        match = next((a for a in agents if a.get("name", "").lower() == target_name.lower()), None)
        if not match:
            # Try partial match
            match = next((a for a in agents if target_name.lower() in a.get("name", "").lower()), None)
        if not match:
            print(f"Error: No agent found matching '{target_name}'")
            print("Run --list to see available agents.")
            sys.exit(1)
        assistant_id = match["assistant_id"]
        agent_name = match.get("name", assistant_id)

    else:
        # Default: demo_IdeageniusWetDryVac
        assistant_id = IDEAGENIUS_WETDRYVAC_ID
        agent_name = "demo_IdeageniusWetDryVac"

    await run_demo(assistant_id, agent_name)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Interrupted.{RESET}")
