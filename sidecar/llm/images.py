"""Screenshots on their way to a model: smaller, and in a turn the provider accepts."""

import base64
import binascii
import io
import logging
from typing import Callable, Iterator

from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

MAX_SIDE = 1568  # larger images are only downscaled by the providers, at a higher token cost
JPEG_QUALITY = 80
SCREENSHOT_NOTE = "Screenshot from the tool result above."


def shrink_screenshot(image_base64: str) -> dict | None:
    """The image as a small JPEG for the model, or None when the bytes aren't an image."""
    try:
        image = Image.open(io.BytesIO(base64.b64decode(image_base64))).convert("RGB")
    except (binascii.Error, OSError, UnidentifiedImageError, ValueError):
        logger.warning("Could not read a screenshot for the model")
        return None
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=JPEG_QUALITY)
    return {"media_type": "image/jpeg", "data": base64.b64encode(output.getvalue()).decode()}


def after_tool_results(messages: list[dict], parts: Callable[[list[dict]], list[dict]]) -> Iterator[dict]:
    """Messages without their images, which follow in a user turn: these providers take only text from a tool.

    The turn waits for the whole run of tool results, since parallel results must stay together.
    """
    waiting: list[dict] = []
    for message in messages:
        if message["role"] != "tool" and waiting:
            yield {"role": "user", "content": parts(waiting)}
            waiting = []
        waiting.extend(message.get("images", []))
        yield {key: value for key, value in message.items() if key != "images"}
    if waiting:
        yield {"role": "user", "content": parts(waiting)}
