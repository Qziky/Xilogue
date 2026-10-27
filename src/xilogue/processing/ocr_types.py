"""Content validation shared by local inference and transactional storage."""

from dataclasses import dataclass
from math import isfinite
from unicodedata import normalize


def normalize_text(text):
    return normalize("NFKC", text).casefold()


@dataclass(frozen=True)
class OcrLine:
    text: str
    box: tuple[tuple[float, float], ...]
    confidence: float


@dataclass(frozen=True)
class OcrDocument:
    engine: str
    lines: tuple[OcrLine, ...]

    @property
    def text(self):
        return "\n".join(line.text for line in self.lines)


def validate_document(document, width, height):
    if not document.engine or len(document.engine) > 200 or len(document.lines) > 4096:
        raise ValueError("invalid_ocr_document")
    if len(document.text) > 1_000_000:
        raise ValueError("ocr_text_too_large")
    for line in document.lines:
        if not isinstance(line.text, str) or not line.text or len(line.text) > 8192:
            raise ValueError("invalid_ocr_text")
        if not isfinite(line.confidence) or not 0 <= line.confidence <= 1 or len(line.box) != 4:
            raise ValueError("invalid_ocr_line")
        for x, y in line.box:
            if not isfinite(x) or not isfinite(y) or not 0 <= x <= width or not 0 <= y <= height:
                raise ValueError("invalid_ocr_coordinates")
