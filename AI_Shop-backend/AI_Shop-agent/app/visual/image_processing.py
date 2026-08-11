from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config.settings import get_settings
from app.harness.agents.contracts import VisualSubject
from app.visual.contracts import VisualProviderError


@dataclass(frozen=True)
class NormalizedImage:
    content: bytes
    mime_type: str
    width: int
    height: int
    sha256: str

    @property
    def data_uri(self) -> str:
        encoded = base64.b64encode(self.content).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"


def normalize_query_image(
    content: bytes,
    selected_subject: VisualSubject | None = None,
) -> NormalizedImage:
    settings = get_settings()
    if not content or len(content) > settings.visual_query_max_bytes:
        raise VisualProviderError("VISUAL_IMAGE_SIZE_INVALID")

    try:
        with Image.open(io.BytesIO(content)) as source:
            width, height = source.size
            if width < 16 or height < 16 or width * height > 25_000_000:
                raise VisualProviderError("VISUAL_IMAGE_DIMENSIONS_INVALID")
            image = ImageOps.exif_transpose(source).convert("RGB")
            if selected_subject is not None:
                image = image.crop(_subject_crop_box(image.size, selected_subject))
            image.thumbnail(
                (settings.visual_query_max_dimension, settings.visual_query_max_dimension),
                Image.Resampling.LANCZOS,
            )
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=90, optimize=True)
            normalized = output.getvalue()
    except VisualProviderError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise VisualProviderError("VISUAL_IMAGE_DECODE_FAILED") from exc

    return NormalizedImage(
        content=normalized,
        mime_type="image/jpeg",
        width=image.width,
        height=image.height,
        sha256=hashlib.sha256(normalized).hexdigest(),
    )


def _subject_crop_box(
    size: tuple[int, int], subject: VisualSubject
) -> tuple[int, int, int, int]:
    width, height = size
    x1, y1, x2, y2 = subject.bbox
    # A small margin preserves contextual edges such as shoe soles and handles.
    margin_x = max(2, round((x2 - x1) * width / 999 * 0.04))
    margin_y = max(2, round((y2 - y1) * height / 999 * 0.04))
    left = max(0, round(x1 * width / 999) - margin_x)
    top = max(0, round(y1 * height / 999) - margin_y)
    right = min(width, round(x2 * width / 999) + margin_x)
    bottom = min(height, round(y2 * height / 999) + margin_y)
    if right - left < 8 or bottom - top < 8:
        raise VisualProviderError("VISUAL_SUBJECT_CROP_TOO_SMALL")
    return left, top, right, bottom
