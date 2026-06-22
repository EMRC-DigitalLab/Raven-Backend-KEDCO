"""
ARIA agent — the core orchestration loop.
Handles conversation management, tool execution, and streaming Claude responses.
"""

import json
import re

import anthropic
from channels.db import database_sync_to_async
from django.conf import settings

from analytics.services.compare_service import get_user_accessible_modules
from aria.models import ARIAConversation, ARIAMessage
from aria.prompts import build_system_prompt
from aria.tools.registry import execute_tool, get_tools_for_modules

_MODEL = 'claude-sonnet-4-6'
_MAX_TOKENS = 4096
_MAX_HISTORY = 10    # messages per conversation sent to Claude
_MAX_ITERATIONS = 8  # max tool-call rounds before forcing a response
_MAX_TOOL_RESULT = 6000  # max chars per tool result before truncation


def _get_client() -> anthropic.Anthropic:
    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        raise ValueError('ANTHROPIC_API_KEY is not configured.')
    return anthropic.Anthropic(api_key=api_key)


def _get_async_client() -> anthropic.AsyncAnthropic:
    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        raise ValueError('ANTHROPIC_API_KEY is not configured.')
    return anthropic.AsyncAnthropic(api_key=api_key)


def _load_history(conversation: ARIAConversation) -> list:
    """Load the last N messages as Anthropic-format message dicts."""
    messages = list(
        conversation.messages.order_by('-created_at')[:_MAX_HISTORY]
    )
    messages.reverse()
    result = []
    for msg in messages:
        result.append({'role': msg.role, 'content': msg.content})
    return result


def _content_to_dict(block) -> dict:
    """Convert an Anthropic SDK content block to a plain dict for re-submission."""
    if hasattr(block, 'type'):
        if block.type == 'text':
            return {'type': 'text', 'text': block.text}
        if block.type == 'tool_use':
            return {
                'type': 'tool_use',
                'id': block.id,
                'name': block.name,
                'input': block.input,
            }
    return {'type': 'text', 'text': str(block)}


def _extract_text(content) -> str:
    """Pull the final text out of a response content list."""
    parts = []
    for block in content:
        if hasattr(block, 'type') and block.type == 'text':
            parts.append(block.text)
        elif isinstance(block, dict) and block.get('type') == 'text':
            parts.append(block.get('text', ''))
    return '\n'.join(parts).strip()


_CHART_RE = re.compile(r'<chart_data>\s*(.*?)\s*</chart_data>', re.DOTALL)


def _cap_tool_result(result_json: str) -> str:
    """Truncate oversized tool results to keep token usage in check."""
    if len(result_json) <= _MAX_TOOL_RESULT:
        return result_json
    return result_json[:_MAX_TOOL_RESULT] + f'\n... [truncated — {len(result_json)} chars total]'


def _split_chart(text: str) -> tuple[str, dict | None]:
    """Extract <chart_data> JSON from response text. Returns (clean_text, chart_dict_or_None)."""
    match = _CHART_RE.search(text)
    if not match:
        return text, None
    clean = _CHART_RE.sub('', text).strip()
    try:
        chart = json.loads(match.group(1))
    except (ValueError, TypeError):
        chart = None
    return clean, chart


def _auto_title(message: str) -> str:
    """Generate a short conversation title from the first user message."""
    return message[:80].strip().rstrip('.?!') or 'New chat'


def run_aria(user, conversation_id, user_message: str) -> dict:
    """
    Main ARIA entry point.
    Returns {'conversation_id': str, 'response': str, 'title': str}
    """
    # Resolve accessible modules
    accessible_modules = get_user_accessible_modules(user)

    # Get or create conversation
    if conversation_id:
        try:
            conversation = ARIAConversation.objects.get(id=conversation_id, user=user)
        except ARIAConversation.DoesNotExist:
            conversation = ARIAConversation.objects.create(
                user=user,
                title=_auto_title(user_message),
            )
    else:
        conversation = ARIAConversation.objects.create(
            user=user,
            title=_auto_title(user_message),
        )

    # Build context
    system_prompt = build_system_prompt(user, accessible_modules)
    tools = get_tools_for_modules(accessible_modules)
    history = _load_history(conversation)

    # Append the new user message to the in-flight history (not saved yet)
    history.append({'role': 'user', 'content': user_message})

    client = _get_client()

    # ── Agentic loop ──────────────────────────────────────────────────────────
    final_text = ''
    iterations = 0

    while iterations < _MAX_ITERATIONS:
        iterations += 1

        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system_prompt,
            messages=history,
            tools=tools,
        )

        if response.stop_reason == 'end_turn':
            final_text = _extract_text(response.content)
            break

        if response.stop_reason == 'tool_use':
            # Collect all tool calls in this turn
            assistant_content = [_content_to_dict(b) for b in response.content]
            tool_results = []

            for block in response.content:
                if hasattr(block, 'type') and block.type == 'tool_use':
                    result_json = execute_tool(block.name, block.input, accessible_modules)
                    tool_results.append({
                        'type': 'tool_result',
                        'tool_use_id': block.id,
                        'content': _cap_tool_result(result_json),
                    })

            # Append assistant turn + tool results to history
            history.append({'role': 'assistant', 'content': assistant_content})
            history.append({'role': 'user', 'content': tool_results})
            continue

        # Any other stop reason — extract what we have
        final_text = _extract_text(response.content)
        break

    if not final_text:
        final_text = "I couldn't generate a response. Please try rephrasing your question."

    final_text, chart_data = _split_chart(final_text)

    # ── Persist both messages ─────────────────────────────────────────────────
    ARIAMessage.objects.create(conversation=conversation, role='user', content=user_message)
    ARIAMessage.objects.create(conversation=conversation, role='assistant', content=final_text)

    # Update conversation timestamp
    conversation.save()

    result = {
        'conversation_id': str(conversation.id),
        'response': final_text,
        'title': conversation.title,
    }
    if chart_data:
        result['chart_data'] = chart_data
    return result


# ── Streaming agent (WebSocket path) ─────────────────────────────────────────

async def stream_aria(user, conversation_id, user_message: str):
    """
    Async generator that yields event dicts for real-time WebSocket streaming.

    Event types:
      {"type": "tool_start", "tool": "<name>"}
      {"type": "tool_end",   "tool": "<name>"}
      {"type": "token",      "text": "<chunk>"}
      {"type": "done",       "conversation_id": "<uuid>", "title": "<str>"}
      {"type": "error",      "message": "<str>"}
    """
    try:
        accessible_modules = await database_sync_to_async(get_user_accessible_modules)(user)

        # Get or create conversation
        if conversation_id:
            try:
                conversation = await database_sync_to_async(
                    ARIAConversation.objects.get
                )(id=conversation_id, user=user)
            except ARIAConversation.DoesNotExist:
                conversation = await database_sync_to_async(ARIAConversation.objects.create)(
                    user=user, title=_auto_title(user_message)
                )
        else:
            conversation = await database_sync_to_async(ARIAConversation.objects.create)(
                user=user, title=_auto_title(user_message)
            )

        system_prompt = build_system_prompt(user, accessible_modules)
        tools = get_tools_for_modules(accessible_modules)
        history = await database_sync_to_async(_load_history)(conversation)
        history.append({'role': 'user', 'content': user_message})

        client = _get_async_client()
        accumulated_text = ''
        iterations = 0

        while iterations < _MAX_ITERATIONS:
            iterations += 1
            round_text = ''

            async with client.messages.stream(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=system_prompt,
                messages=history,
                tools=tools,
            ) as stream:
                async for text in stream.text_stream:
                    round_text += text
                    yield {'type': 'token', 'text': text}

                final = await stream.get_final_message()

            accumulated_text += round_text

            if final.stop_reason == 'end_turn':
                break

            if final.stop_reason == 'tool_use':
                assistant_content = [_content_to_dict(b) for b in final.content]
                tool_results = []

                for block in final.content:
                    if hasattr(block, 'type') and block.type == 'tool_use':
                        yield {'type': 'tool_start', 'tool': block.name}
                        result_json = await database_sync_to_async(execute_tool)(
                            block.name, block.input, accessible_modules
                        )
                        yield {'type': 'tool_end', 'tool': block.name}
                        tool_results.append({
                            'type': 'tool_result',
                            'tool_use_id': block.id,
                            'content': _cap_tool_result(result_json),
                        })

                history.append({'role': 'assistant', 'content': assistant_content})
                history.append({'role': 'user', 'content': tool_results})
                continue

            break

        raw_text = accumulated_text.strip() or "I couldn't generate a response. Please try again."
        final_text, chart_data = _split_chart(raw_text)

        # Persist
        await database_sync_to_async(ARIAMessage.objects.create)(
            conversation=conversation, role='user', content=user_message
        )
        await database_sync_to_async(ARIAMessage.objects.create)(
            conversation=conversation, role='assistant', content=final_text
        )
        await database_sync_to_async(conversation.save)()

        if chart_data:
            yield {'type': 'chart_data', 'chart': chart_data}

        yield {'type': 'done', 'conversation_id': str(conversation.id), 'title': conversation.title}

    except Exception as e:
        yield {'type': 'error', 'message': str(e)}
