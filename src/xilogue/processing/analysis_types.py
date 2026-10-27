"""Bounded structured output and exact evidence quotations; no executable model output."""

import math

ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 1200},
        "topics": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 40}},
        "keywords": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 40}},
        "importance": {"type": "number", "minimum": 0, "maximum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "uncertainties": {
            "type": "array",
            "maxItems": 6,
            "items": {"type": "string", "maxLength": 200},
        },
        "claims": {
            "type": "array",
            "minItems": 1,
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string", "maxLength": 400},
                    "source_id": {"type": "string"},
                    "quote": {"type": "string", "minLength": 1, "maxLength": 240},
                },
                "required": ["text", "source_id", "quote"],
            },
        },
    },
    "required": [
        "summary",
        "topics",
        "keywords",
        "importance",
        "confidence",
        "uncertainties",
        "claims",
    ],
}


def validate_analysis(data, evidence):
    def string(value, maximum):
        return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum

    if not isinstance(data, dict) or set(data) != set(ANALYSIS_SCHEMA["required"]):
        raise ValueError("invalid_analysis_shape")
    if not string(data["summary"], 1200):
        raise ValueError("invalid_summary")
    for key, count, length in (("topics", 8, 40), ("keywords", 12, 40), ("uncertainties", 6, 200)):
        if (
            not isinstance(data[key], list)
            or len(data[key]) > count
            or any(not string(v, length) for v in data[key])
        ):
            raise ValueError("invalid_analysis_list")
    for key in ("importance", "confidence"):
        if (
            type(data[key]) not in (int, float)
            or not math.isfinite(data[key])
            or not 0 <= data[key] <= 1
        ):
            raise ValueError("invalid_analysis_score")
    if not isinstance(data["claims"], list) or not 1 <= len(data["claims"]) <= 6:
        raise ValueError("analysis_requires_evidence")
    sources = {item["id"]: item["text"] for item in evidence}
    for claim in data["claims"]:
        if (
            not isinstance(claim, dict)
            or set(claim) != {"text", "source_id", "quote"}
            or not string(claim["text"], 400)
            or not string(claim["source_id"], 64)
            or not string(claim["quote"], 240)
            or claim["source_id"] not in sources
            or claim["quote"] not in sources[claim["source_id"]]
        ):
            raise ValueError("analysis_evidence_rejected")
    return data
