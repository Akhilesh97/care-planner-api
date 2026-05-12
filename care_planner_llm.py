"""
care_planner_llm.py — LLM abstraction for the Care Planner API
===============================================================
Switch providers via LLM_PROVIDER env var (default: openai).

    LLM_PROVIDER=openai    → uses OPENAI_API_KEY  + OPENAI_MODEL  (default gpt-4o-mini)
    LLM_PROVIDER=gemini    → uses GEMINI_API_KEY  + GEMINI_MODEL  (default gemini-1.5-flash)

Usage:
    from care_planner_llm import call_llm
    data = call_llm(prompt_string, json_schema_dict)
"""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv
load_dotenv()  # must run before LLM_PROVIDER is read at module level

# ─── Provider configuration (set in .env) ───────────────────────────────────

LLM_PROVIDER  = os.getenv("LLM_PROVIDER",  "openai").lower()        # "openai" | "gemini"
OPENAI_MODEL  = os.getenv("OPENAI_MODEL",  "gpt-4o-mini")           # cheapest capable model
GEMINI_MODEL  = os.getenv("GEMINI_MODEL",  "gemini-2.0-flash")      # stable, cheap Gemini model

# ─── Schema helpers ──────────────────────────────────────────────────────────

_GEMINI_STRIP = {"additionalProperties", "minItems", "maxItems"}

def _simplify_for_gemini(schema: dict) -> dict:
    """Recursively remove keys Gemini rejects or that cause 'too many states' errors.

    Strips: additionalProperties (OpenAI-only), minItems/maxItems (causes complexity
    explosion in nested schemas). Counts are enforced by the prompt instead.
    """
    cleaned = {k: v for k, v in schema.items() if k not in _GEMINI_STRIP}
    if "properties" in cleaned:
        cleaned["properties"] = {
            k: _simplify_for_gemini(v) if isinstance(v, dict) else v
            for k, v in cleaned["properties"].items()
        }
    if "items" in cleaned and isinstance(cleaned["items"], dict):
        cleaned["items"] = _simplify_for_gemini(cleaned["items"])
    return cleaned

# ─── Public interface ────────────────────────────────────────────────────────

def call_llm(prompt: str, schema: dict) -> dict:
    """
    Send prompt to the configured LLM and return a parsed JSON dict.

    Both providers are asked to return structured JSON matching `schema`.
    Raises ValueError if the required API key is missing.
    Raises RuntimeError on LLM call failure.
    """
    if LLM_PROVIDER == "gemini":
        return _call_gemini(prompt, _simplify_for_gemini(schema))
    return _call_openai(prompt, schema)


# ─── OpenAI ──────────────────────────────────────────────────────────────────

def _call_openai(prompt: str, schema: dict) -> dict:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("openai package not installed. Run: pip install openai>=1.0.0") from e

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set in environment / .env file.")

    client = OpenAI(api_key=api_key)

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a specialist child development therapist. "
                    "Always respond with valid JSON that matches the schema exactly. "
                    "No markdown, no explanation, just the JSON object."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "care_plan",
                "strict": True,
                "schema": schema,
            },
        },
        temperature=0.75,
        max_tokens=4096,
    )

    raw = response.choices[0].message.content
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"OpenAI returned invalid JSON: {e}\nRaw: {raw[:300]}") from e


# ─── Gemini ──────────────────────────────────────────────────────────────────

def _call_gemini(prompt: str, schema: dict) -> dict:
    try:
        from google import genai
    except ImportError as e:
        raise RuntimeError("google-genai package not installed. Run: pip install google-genai>=0.5.0") from e

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set in environment / .env file.")

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=genai.types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.75,
            max_output_tokens=4096,
        ),
    )

    try:
        return json.loads(response.text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Gemini returned invalid JSON: {e}\nRaw: {response.text[:300]}") from e
