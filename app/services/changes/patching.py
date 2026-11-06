"""Turning a request into new file content with the LLM. Shared by change requests and fix sessions.

Nothing here touches the disk: callers decide where the result goes (a proposal for review,
or a sandbox copy for testing).
"""

import difflib
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.core.secrets import REDACTED, redact
from app.services.changes.edits import Edit, apply_edits, extract_code_block, parse_edit_blocks
from app.services.llm_service import ChatMessage, LLMService

EDIT_SYSTEM = (
    "You modify source code. Make the smallest change that fulfils the request, keep the existing style, "
    "and do not touch unrelated code.\n"
    "Reply with one or more edit blocks in exactly this format:\n"
    "<<<<<<< SEARCH\n"
    "<lines copied exactly from the file, including indentation; enough lines to be unique>\n"
    "=======\n"
    "<the new lines>\n"
    ">>>>>>> REPLACE\n"
    "Then write 'EXPLANATION:' followed by one or two sentences. Never output the whole file. "
    "If this file needs no change, reply only with 'EXPLANATION: no change needed'."
)

NEW_FILE_SYSTEM = (
    "You create a new source file. Reply with the complete file in one fenced code block, "
    "then 'EXPLANATION:' followed by one or two sentences."
)

WHOLE_FILE_MIN_SIMILARITY = 0.6


def whole_file_fallback(original: str, reply: str) -> tuple[str, str] | None:
    """Accept a reply that is the complete edited file instead of edit blocks.

    Small models often ignore the edit-block format. The rewrite is accepted only when it
    stays close to the original, so a reply that drops or rewrites large parts is refused.
    Returns (new content, reviewer note) or None.
    """
    if "```" not in reply:
        return None
    candidate = extract_code_block(reply)
    if "\r\n" in original:
        candidate = candidate.replace("\r\n", "\n").replace("\n", "\r\n")
    similarity = difflib.SequenceMatcher(None, original, candidate).ratio()
    if similarity < WHOLE_FILE_MIN_SIMILARITY:
        return None
    note = (
        f"The model returned the whole file instead of edit blocks (similarity {similarity:.0%}); "
        "review every line of the diff."
    )
    return candidate, note


def without_placeholders(edits: list[Edit]) -> tuple[list[Edit], list[str]]:
    """Drop edits that would write the redaction placeholder into the file instead of the real secret."""
    kept = [e for e in edits if REDACTED not in e.replace]
    blocked = len(edits) - len(kept)
    return kept, [f"{blocked} edit(s) touched a masked secret and were skipped; edit those lines by hand."] if blocked else []


@dataclass
class FilePatch:
    path: str
    original: str | None
    new_content: str
    explanation: str
    model: str
    notes: list[str] = field(default_factory=list)


async def generate_file_patch(
    llm: LLMService, request: str, path: str, original: str | None, other_paths: Sequence[str] = ()
) -> FilePatch:
    """Ask the model for edit blocks for one file and apply them in memory (one retry on mismatch)."""
    others = ", ".join(other_paths)
    context = f"Request: {request}\n" + (f"Other files in this change: {others}\n" if others else "")

    if original is None:
        reply = await llm.chat([
            ChatMessage("system", NEW_FILE_SYSTEM), ChatMessage("user", f"{context}\nCreate the file: {path}"),
        ])
        _, explanation = parse_edit_blocks(reply.content)
        return FilePatch(path, None, extract_code_block(reply.content), explanation, reply.model)

    # The model sees the file with secrets masked; edits are applied to the real file.
    shown = redact(original)
    prompt = f"{context}\nFile: {path}\n```\n{shown.text}\n```"
    reply = await llm.chat([ChatMessage("system", EDIT_SYSTEM), ChatMessage("user", prompt)])
    edits, explanation = parse_edit_blocks(reply.content)
    if not edits and shown.count == 0 and (whole := whole_file_fallback(original, reply.content)) is not None:
        return FilePatch(path, original, whole[0], explanation, reply.model, [whole[1]])
    edits, blocked = without_placeholders(edits)
    outcome = apply_edits(original, edits)
    if outcome.failures:
        # One retry: tell the model exactly which blocks did not match.
        retry = (
            f"{prompt}\n\nSome of your edit blocks could not be applied:\n" + "\n".join(outcome.failures)
            + "\nReply again with corrected edit blocks for the whole request. "
            "Copy SEARCH lines exactly from the file above."
        )
        reply = await llm.chat([ChatMessage("system", EDIT_SYSTEM), ChatMessage("user", retry)])
        edits, explanation = parse_edit_blocks(reply.content)
        edits, blocked = without_placeholders(edits)
        outcome = apply_edits(original, edits)
    notes = outcome.failures + blocked + ([] if edits or blocked else ["The model returned no edit blocks"])
    if shown.count:
        notes.append(f"{shown.count} secret value(s) were masked before the model saw this file.")
    return FilePatch(path, original, outcome.content, explanation, reply.model, notes)
