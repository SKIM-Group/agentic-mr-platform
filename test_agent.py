#!/usr/bin/env python3
"""
Interactive terminal chat against the backend /api/chat SSE endpoint.
Keeps the same conversation_id across turns so the backend retains history.

Usage:
  python3 test_agent.py          # new conversation
  python3 test_agent.py <id>     # resume an existing conversation by ID

Commands:
  /new    — start a fresh conversation
  /id     — print current conversation ID
  /quit   — exit
"""

import sys
import json
import uuid
import httpx

BASE_URL = "http://localhost:8000"
API_KEY  = "dev-key"

# ANSI colours
R = "\033[0m"
BOLD    = "\033[1m"
CYAN    = "\033[36m"
MAGENTA = "\033[35m"
YELLOW  = "\033[33m"
GREEN   = "\033[32m"
RED     = "\033[31m"
GREY    = "\033[90m"
BLUE    = "\033[34m"


def send(conversation_id: str, message: str) -> None:
    with httpx.Client(timeout=300) as client:
        with client.stream(
            "POST",
            f"{BASE_URL}/api/chat",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            json={"conversation_id": conversation_id, "message": message},
        ) as resp:
            resp.raise_for_status()

            buf = ""
            in_token_stream = False

            for chunk in resp.iter_text():
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type", "")

                    if etype == "token":
                        if not in_token_stream:
                            print(f"\n{BLUE}◆{R} ", end="", flush=True)
                            in_token_stream = True
                        print(event.get("text", ""), end="", flush=True)

                    else:
                        if in_token_stream:
                            print()  # newline after streamed text
                            in_token_stream = False

                        if etype == "status":
                            print(f"{CYAN}  ⟳  {event.get('text', '')}{R}")

                        elif etype == "pipeline_planned":
                            steps = event.get("pipeline", [])
                            names = " → ".join(
                                s.get("display_name", s.get("name", "?"))
                                for s in steps
                            )
                            print(f"{MAGENTA}  ⚡ Pipeline: {names}{R}")

                        elif etype == "pipeline_transition":
                            print(f"{MAGENTA}  ↪  transitioning to next skill…{R}")

                        elif etype == "skill_start":
                            step  = event.get("step", 0)
                            total = event.get("total", 1)
                            name  = event.get("display_name") or event.get("skill_name", "?")
                            print(f"{YELLOW}  ▶  [{step+1}/{total}] {name}{R}")

                        elif etype == "skill_complete":
                            name = event.get("display_name") or event.get("skill_name", "?")
                            print(f"{GREEN}  ✓  {name} done{R}")

                        elif etype == "tool_used":
                            tool = event.get("tool", "?")
                            detail = event.get("query") or event.get("filename") or event.get("language") or ""
                            print(f"{CYAN}  🔧 {tool}: {detail}{R}")

                        elif etype == "file_saved":
                            name = event.get("filename", "?")
                            path = event.get("path", "")
                            size = event.get("size", 0)
                            print(f"{GREEN}  💾 Saved: {name}  ({size} bytes)  →  {path}{R}")

                        elif etype == "error":
                            print(f"{RED}  ✗  {event.get('text', '')}{R}")

                        elif etype == "done":
                            pass  # no noise

            if in_token_stream:
                print()


def repl(conversation_id: str) -> None:
    print(f"\n{BOLD}Skills Chat{R}  {GREY}(conversation: {conversation_id}){R}")
    print(f"{GREY}Commands: /new  /id  /quit{R}\n")

    while True:
        try:
            user_input = input(f"{BOLD}You:{R} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue

        if user_input == "/quit":
            print("Bye!")
            break

        if user_input == "/new":
            conversation_id = str(uuid.uuid4())
            print(f"{GREY}New conversation: {conversation_id}{R}\n")
            continue

        if user_input == "/id":
            print(f"{GREY}Conversation ID: {conversation_id}{R}\n")
            continue

        try:
            send(conversation_id, user_input)
        except httpx.HTTPStatusError as e:
            print(f"{RED}HTTP {e.response.status_code}: {e.response.text[:200]}{R}")
        except httpx.ConnectError:
            print(f"{RED}Cannot connect to {BASE_URL} — is the backend running?{R}")

        print()  # blank line between turns


if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else str(uuid.uuid4())
    repl(cid)
