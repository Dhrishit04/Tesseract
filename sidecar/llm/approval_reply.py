"""Read a typed answer to an approval card ("yes honey", "nah wait") as allow, always, deny or other."""

import logging
import string

from llm.openrouter import OpenRouterClient
from permissions import ApprovalRequest, Verdict

logger = logging.getLogger(__name__)

CLASSIFY_MARKER = "Classify the owner's reply to an approval request."
VERDICTS = ("allow", "always", "deny", "other")


def _prompt(request: ApprovalRequest) -> str:
    # Title only: the summary carries model-written arguments that could steer the verdict.
    return (
        f"{CLASSIFY_MARKER} Tesseract asked whether it may: {request.title}.\n"
        "Answer with exactly one word:\n"
        "allow: yes, this time\n"
        "always: yes, and don't ask again for this kind of action\n"
        "deny: no, or wait\n"
        "other: anything else, including a new instruction"
    )


async def classify_reply(client: OpenRouterClient, model: str, request: ApprovalRequest, text: str) -> Verdict:
    """The owner's verdict; anything unclear or any failure is "other", never "allow"."""
    messages = [{"role": "system", "content": _prompt(request)}, {"role": "user", "content": text}]
    try:
        result = await client.chat(messages, model=model)
        answer = "" if result.get("error") else result["choices"][0]["message"].get("content") or ""
    except Exception:
        logger.exception("Could not classify an approval reply")
        return "other"
    words = answer.lower().translate(str.maketrans("", "", string.punctuation)).split()
    return words[0] if words and words[0] in VERDICTS else "other"
