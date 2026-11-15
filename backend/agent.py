"""Shared Claude tool-use loop for the coach and nutrition agents.

Both agents work the same way: send the conversation to Claude with a set of
tools, run any tool calls the model makes, feed the results back, and repeat
until the model returns text. Only the model, system prompt, tools, and tool
dispatcher differ — so that loop lives here once instead of being copy-pasted.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# A tool dispatcher: takes (tool_name, input_dict) and returns a JSON string.
ToolRunner = Callable[[str, dict[str, Any]], str]


def run_tool_loop(
    *,
    client: Any,
    model: str,
    system_text: str,
    tools: list[dict[str, Any]],
    run_tool: ToolRunner,
    messages: list[dict[str, Any]],
    max_iterations: int,
    label: str,
    max_tokens: int = 4096,
    **create_kwargs: Any,
) -> str:
    """Drive a tool-use conversation to completion and return the final text.

    ``messages`` is mutated in place (assistant turns and tool results are
    appended). ``create_kwargs`` are passed straight through to
    ``client.messages.create`` so each caller can add its own options (e.g. the
    coach's extended-thinking config). The system prompt is cached via
    ``cache_control`` since it is constant across the loop.
    """
    for iteration in range(max_iterations):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system_text,
                     "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            messages=messages,
            **create_kwargs,
        )
        # Preserve the full assistant turn (tool_use blocks included) for context.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                try:
                    result = run_tool(block.name, dict(block.input or {}))
                    is_error = False
                except Exception as e:  # noqa: BLE001
                    logger.exception("%s tool %s failed", label, block.name)
                    result = json.dumps({"error": f"{type(e).__name__}: {e}"})
                    is_error = True
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": tool_results})
            continue

        if response.stop_reason == "pause_turn":
            # Server-side tool sampling paused; re-send to continue.
            continue

        # end_turn (or anything else) — extract the text and return.
        text_blocks = [b.text for b in response.content
                       if getattr(b, "type", None) == "text"]
        if response.stop_reason != "end_turn":
            logger.warning("%s: unexpected stop_reason %r at iteration %d",
                           label, response.stop_reason, iteration)
        return "\n\n".join(text_blocks).strip() or "(empty response)"

    logger.warning("%s loop hit max iterations (%d) without finishing", label, max_iterations)
    return "(the assistant hit its tool-use limit — try a simpler request)"
