"""
The Vasco agent loop: plan -> call tools -> observe -> repeat -> answer.

`run_agent` is an async generator of JSON-serialisable events, streamed to the
browser as Server-Sent Events:

    start        {run_id, provider, model, tools}
    reasoning    {text}                      model thinking (delta)
    text         {text}                      answer text (delta)
    tool_start   {id, name, label, icon, args, step}
    tool_end     {id, ok, summary, preview, duration_ms, sources, image_url}
    notice       {message}
    done         {content, sources, steps, tool_calls, usage, elapsed_ms, provider_used, model_used}
    error        {message}
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from backend.agent.llm import BaseAdapter, ProviderError, ToolCall, build_adapter
from backend.agent.tools import TOOLS, ToolOutput, available_tools, execute_tool
from backend.ai_providers import _call_yqcloud_fast
from backend.darija import (
    AUTO_MULTILINGUAL_AND_DARIJA_PROMPT,
    DARIJA_DETECTED_PROMPT,
    detect_conversation_language,
)

MAX_STEPS = 8
FINAL_STEP_NOTE = (
    "[System note] Tool-use budget reached for this request. Do not call any more tools: "
    "write your final answer now using the information gathered so far, and say clearly if something "
    "could not be verified."
)


def build_compact_system_prompt(custom: str, use_darija: bool, image_hint: bool) -> str:
    """Short prompt for small local models: on a CPU every prompt token costs ~50 ms to read."""
    today = dt.datetime.now().strftime("%A %d %B %Y")
    prompt = (
        f"You are Vasco, an AI agent. Today is {today}.\n"
        "RULES:\n"
        "1. If the user asks for ANY number to compute (loan, percentage, total, conversion), you MUST call the "
        "calculator tool first, once per number, BEFORE writing the answer. Never compute in your head.\n"
        "2. For news, current facts, prices or people: call web_search first (kind='news' for news, no dates in the query).\n"
        "3. Simple or conversational message: answer directly, no tools.\n"
        "4. Only cite links returned by web_search. Never invent a source, URL or number.\n"
        "5. Answer in the user's language (French, Moroccan Darija, Arabic or English), short, in markdown. "
        "Never use LaTeX: write formulas as plain text.\n"
    )
    if use_darija:
        prompt += "- The user writes in Moroccan Darija: answer naturally in Darija, preferably in Arabic script.\n"
    if image_hint:
        prompt += "- The user wants an image: use generate_image with a detailed English prompt.\n"
    if custom.strip():
        prompt += f"\nUser instructions:\n{custom.strip()}\n"
    return prompt


def build_system_prompt(custom: str, tool_names: List[str], use_darija: bool, image_hint: bool) -> str:
    today = dt.datetime.now().strftime("%A %d %B %Y")
    tool_lines = "\n".join(f"- {n}: {TOOLS[n].description}" for n in tool_names)
    prompt = f"""You are **Vasco**, an autonomous AI agent. Today is {today}.

You solve the user's request end to end by reasoning and using tools, then give a clear, well-structured final answer.

## How you work
1. Understand what the user actually needs. If the request is simple or conversational, just answer — don't use tools for the sake of it.
2. For anything factual, current, numeric or verifiable, use your tools instead of guessing. Your training data is out of date: for news, prices, scores, releases, laws, people or anything time-sensitive, search first.
   Never do arithmetic in your head: every number you report that comes from a calculation (totals, interest, percentages, conversions) must come from a calculator or run_python result. Plan all the calculations you will need and make those calls together.
3. Break multi-part tasks into steps. You may call several independent tools at once (in parallel).
4. Read tool results critically. If results are thin or conflicting, refine the query, or read the most relevant page with fetch_url.
5. Stop as soon as you have enough to answer well. Never invent tool results, URLs, quotes or numbers.
6. For "latest news" / "today" questions, report the most recent items you found, each with its date and source. Never answer that there is no news: if nothing is from today, give the latest items and say when they were published.

## Tools available
{tool_lines}

## Final answer
- Answer in the user's language (see language rules below).
- Lead with the answer, then the supporting details. Use markdown headings, lists and tables when they help; keep casual replies short.
- When you used web sources, cite them inline as markdown links, e.g. [Le Monde](https://...).
- For code, give complete, runnable code in fenced blocks with the language tag. Single-file HTML/CSS/JS apps can be previewed live by the user.
- Do not use LaTeX ($...$, \\[...\\]): the chat does not render it. Write formulas in plain text or inline code, e.g. `M = P·r / (1 − (1+r)^−n)`.
- If something could not be verified, say so plainly.
"""
    if image_hint and "generate_image" in tool_names:
        prompt += "\nThe user has switched on image mode: they want an image. Use generate_image with a rich English prompt.\n"
    if custom.strip():
        prompt += f"\n## Instructions from the user (follow them)\n{custom.strip()}\n"
    prompt += f"\n{AUTO_MULTILINGUAL_AND_DARIJA_PROMPT}"
    if use_darija:
        prompt += f"\n{DARIJA_DETECTED_PROMPT}"
    return prompt


def _args_for_display(args: Any) -> Dict[str, Any]:
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            return {"raw": args[:500]}
    if not isinstance(args, dict):
        return {}
    shown = {}
    for k, v in args.items():
        shown[k] = v if not isinstance(v, str) or len(v) <= 4000 else v[:4000] + "…"
    return shown


def _repair_tool_name(name: str, allowed: List[str]) -> str:
    """Small models sometimes leak channel tokens into names (e.g. 'web_searchcommentary')."""
    name = (name or "").strip()
    if name in allowed:
        return name
    for candidate in sorted(allowed, key=len, reverse=True):
        if name.startswith(candidate) or name.endswith(candidate):
            return candidate
    return name


async def _run_tool(call: ToolCall, allowed: List[str]) -> tuple[ToolCall, ToolOutput, int]:
    started = time.perf_counter()
    out = await execute_tool(call.name, call.arguments, allowed)
    return call, out, int((time.perf_counter() - started) * 1000)


async def run_agent(*, provider: str, model: str, messages: List[Dict[str, Any]],
                    attachments: List[Dict[str, Any]], deep_think: bool, web_search: bool,
                    image_mode: bool, custom_system_prompt: str,
                    max_tool_rounds: Optional[int] = None,
                    fast: bool = False) -> AsyncIterator[Dict[str, Any]]:
    run_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    tools = available_tools(web=web_search, images=True)
    tool_names = [t.name for t in tools]

    lang = detect_conversation_language(messages)
    if provider == "ollama":  # local CPU/GPU models: keep the prompt short
        system = build_compact_system_prompt(custom_system_prompt, bool(lang.get("is_darija")), image_mode)
    else:
        system = build_system_prompt(custom_system_prompt, tool_names, bool(lang.get("is_darija")), image_mode)

    yield {"type": "start", "run_id": run_id, "provider": provider, "model": model, "tools": tool_names}

    try:
        adapter: BaseAdapter = build_adapter(provider=provider, model=model, system=system, history=messages,
                                             attachments=attachments, deep_think=deep_think)
    except ProviderError as e:
        yield {"type": "error", "message": str(e)}
        return

    answer_parts: List[str] = []
    sources: List[Dict[str, Any]] = []
    seen_urls = set()
    usage = {"input_tokens": 0, "output_tokens": 0}
    tool_call_count = 0
    offer_tools: Optional[list] = tools
    step = 0

    if fast:
        adapter.use_fast_mode()
    max_rounds = min(adapter.max_tool_rounds, MAX_STEPS - 1)
    if max_tool_rounds is not None:  # e.g. voice mode: answer quickly
        max_rounds = max(1, min(max_rounds, max_tool_rounds))
    try:
        while step < MAX_STEPS:
            step += 1
            final_turn = step > max_rounds
            turn_tools = None if (final_turn and adapter.can_drop_tools) else offer_tools
            hint = adapter.wait_hint(step)
            if hint:
                yield {"type": "status", "message": hint}
            try:
                async for ev in adapter.stream_turn(turn_tools):
                    if ev["type"] == "text":
                        answer_parts.append(ev["text"])
                    yield ev
            except ProviderError as e:
                if e.tools_unsupported and offer_tools:
                    # Model can't call tools (e.g. some local Ollama models): answer directly instead.
                    offer_tools = None
                    step -= 1
                    yield {"type": "notice", "message": "Ce modèle ne supporte pas les outils : réponse directe."}
                    continue
                if provider == "vasco_free" and step == 1:
                    # Free engine unavailable: degrade to the backup gateway (no tools).
                    yield {"type": "notice", "message": "Moteur principal indisponible — bascule sur le moteur de secours (sans outils)."}
                    _, reply = await _call_yqcloud_fast(messages, attachments, system)
                    answer_parts.append(reply)
                    yield {"type": "text", "text": reply}
                    break
                raise

            result = adapter.last_result
            for k in usage:
                usage[k] += result.usage.get(k, 0)

            if not result.tool_calls:
                break

            if final_turn:
                if not "".join(answer_parts).strip():
                    yield {"type": "notice", "message": "Limite d'étapes atteinte avant une réponse complète."}
                break

            for call in result.tool_calls:
                call.name = _repair_tool_name(call.name, tool_names)
                spec = TOOLS.get(call.name)
                yield {
                    "type": "tool_start", "id": call.id, "name": call.name, "step": step,
                    "label": spec.label if spec else call.name, "icon": spec.icon if spec else "🛠️",
                    "args": _args_for_display(call.arguments),
                }

            outputs = []
            for coro in asyncio.as_completed([_run_tool(c, tool_names) for c in result.tool_calls]):
                call, out, ms = await coro
                tool_call_count += 1
                outputs.append((call, out))
                new_sources = []
                for s in out.sources:
                    if s.get("url") and s["url"] not in seen_urls:
                        seen_urls.add(s["url"])
                        sources.append(s)
                        new_sources.append(s)
                yield {
                    "type": "tool_end", "id": call.id, "ok": out.ok, "summary": out.summary,
                    "preview": out.preview[:2000], "duration_ms": ms, "sources": new_sources,
                    "image_url": out.image_url,
                }

            # Keep results in the same order as the calls (required by some providers).
            order = {c.id: i for i, c in enumerate(result.tool_calls)}
            outputs.sort(key=lambda pair: order.get(pair[0].id, 0))
            note = FINAL_STEP_NOTE if step + 1 > max_rounds else ""
            adapter.append_tool_results(result, outputs, note=note)
            if answer_parts and not answer_parts[-1].endswith("\n"):
                answer_parts.append("\n\n")

    except ProviderError as e:
        yield {"type": "error", "message": str(e)}
        return
    except httpx.ConnectError:
        hint = " Vérifiez qu'Ollama est lancé (ollama serve) et l'URL dans « Clés API »." if provider == "ollama" else ""
        yield {"type": "error", "message": f"Impossible de joindre {adapter.provider_label}.{hint}"}
        return
    except httpx.TimeoutException:
        yield {"type": "error", "message": f"{adapter.provider_label} n'a pas répondu à temps. Réessayez."}
        return
    except Exception as e:
        yield {"type": "error", "message": f"Erreur interne de l'agent : {type(e).__name__}: {e}"}
        return

    content = "".join(answer_parts).strip()
    yield {
        "type": "done",
        "content": content,
        "sources": sources,
        "steps": step,
        "tool_calls": tool_call_count,
        "usage": usage,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "provider_used": adapter.provider_label,
        "model_used": adapter.model if provider != "vasco_free" else "gpt-oss (Vasco Cloud)",
    }
