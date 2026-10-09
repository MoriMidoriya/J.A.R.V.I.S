"""Long-term memory tools: remember / recall / forget facts about the user.

Remembered facts are injected into the system prompt (stable context) so Jarvis simply *knows* them; `recall` is
for explicit questions ("what did I tell you about…") and for when the list is long.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from jarvis.llm.context import register_context_provider
from jarvis.memory.store import get_store
from jarvis.tools.registry import ToolError, tool

MAX_FACTS_IN_PROMPT = 20


def _facts_context() -> str | None:
    facts = get_store().list_facts(MAX_FACTS_IN_PROMPT)
    if not facts:
        return None
    return "- Things the user told you to remember:\n" + "\n".join(f"  • {f.text}" for f in reversed(facts))


register_context_provider(_facts_context, stable=True)


@tool(summary="Remember: {fact}")
def remember(fact: Annotated[str, Field(description="The fact to store, as a short third-person statement, e.g. 'Mori's car is parked on level 3', 'Mori prefers tea over coffee'")]) -> str:
    """Store a fact permanently (preferences, where things are, names, dates). Use when the user says 'remember…' or shares something worth keeping."""
    f = get_store().add_fact(fact)
    return f"Remembered (#{f.id}): {f.text}"


@tool(summary="Recall: {query}")
def recall(query: Annotated[str | None, Field(description="What to look for; null = list everything remembered")] = None) -> list[str]:
    """Search remembered facts. Only needed if the answer isn't already in your context."""
    st = get_store()
    facts = st.search_facts(query, 8) if query else st.list_facts(30)
    if not facts:
        return ["Nothing remembered" + (f" about '{query}'." if query else " yet.")]
    return [f"#{f.id} ({f.created_at:%d %b}): {f.text}" for f in facts]


@tool(summary="Forget: {what}")
def forget(what: Annotated[str, Field(description="Fact id like '#12', or words from the fact to forget")]) -> str:
    """Delete a remembered fact."""
    st = get_store()
    w = what.strip().lstrip("#")
    if w.isdigit():
        if st.delete_fact(int(w)):
            return f"Forgotten #{w}."
        raise ToolError(f"No fact #{w}.")
    matches = st.search_facts(what, 3)
    if not matches:
        raise ToolError(f"Nothing remembered matches '{what}'.")
    if len(matches) > 1 and matches[0].text.lower() != what.lower():
        return "Which one? " + "; ".join(f"#{f.id}: {f.text}" for f in matches)
    st.delete_fact(matches[0].id)
    return f"Forgotten: {matches[0].text}"
