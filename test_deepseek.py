#!/usr/bin/env python3
"""Interactive DeepSeek R1 chat via AWS Bedrock."""

import sys
import boto3
from botocore.exceptions import ClientError

client = boto3.client("bedrock-runtime", region_name="us-west-2")
MODEL  = "us.deepseek.r1-v1:0"

messages = []

print("DeepSeek R1 on Bedrock  (type /quit to exit, /clear to reset)\n")

while True:
    try:
        user = input("You: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nBye!")
        break

    if not user:
        continue
    if user == "/quit":
        break
    if user == "/clear":
        messages.clear()
        print("Conversation cleared.\n")
        continue

    messages.append({"role": "user", "content": [{"text": user}]})

    try:
        resp = client.converse(
            modelId=MODEL,
            messages=messages,
            inferenceConfig={"maxTokens": 8192, "temperature": 0.6},
        )
    except ClientError as e:
        print("Error:", e.response["Error"]["Message"], "\n")
        messages.pop()
        continue

    content = resp["output"]["message"]["content"]

    # Print reasoning first if present
    for block in content:
        if "reasoningContent" in block:
            thinking = block["reasoningContent"]["reasoningText"]["text"]
            print(f"\n\033[90m[thinking]\n{thinking}\033[0m")

    answer = ""
    for block in content:
        if "text" in block:
            answer = block["text"]
            print(f"\nR1: {answer}\n")

    usage = resp["usage"]
    print(f"\033[90m({usage['inputTokens']} in / {usage['outputTokens']} out tokens)\033[0m\n")

    # Keep assistant turn in history for multi-turn
    messages.append({"role": "assistant", "content": [{"text": answer}]})
