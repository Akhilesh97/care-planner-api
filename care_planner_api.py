"""
care_planner_api.py — 7-Day Care Planner API
=============================================
Generates a personalised 7-day activity plan for a child based on:
  - Assessment mode (LEARN / MONITOR / CONCERN)
  - Age in months
  - Three domain Z-scores (< 65 = needs improvement → priority focus)
  - Day 1 activities provided by caller (3 activities)

Output: Days 1–7, 3 activities each, with title, description, steps,
        duration, tip, image URL (Unsplash), and image search keywords.

Run:    uvicorn care_planner_api:app --reload --port 8001
Docs:   http://localhost:8001/docs

LLM provider is configured in care_planner_llm.py via LLM_PROVIDER env var.
    LLM_PROVIDER=openai  (default) → uses OPENAI_API_KEY
    LLM_PROVIDER=gemini            → uses GEMINI_API_KEY
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from care_planner_llm import call_llm, LLM_PROVIDER, OPENAI_MODEL, GEMINI_MODEL

load_dotenv()

app = FastAPI(
    title="Neev Care Planner API",
    description=(
        "Generates a 7-day personalised home activity plan for children "
        "based on developmental domain Z-scores."
    ),
    version="1.0.0",
)

# ─── Constants ─────────────────────────────────────────────────────────────

ZSCORE_THRESHOLD = 65.0  # below this → domain is a priority focus

MODE_CONTEXT = {
    "LEARN":    "routine monitoring — child is developing normally, use enrichment activities",
    "MONITOR":  "early gaps detected — light preventative activities embedded in daily routine",
    "CONCERN":  "significant gaps flagged — structured, targeted daily skill-building",
}

# ─── Request Models ─────────────────────────────────────────────────────────

class DomainScore(BaseModel):
    name: str = Field(..., examples=["Motor Development"])
    zscore: float = Field(..., ge=0.0, le=100.0, examples=[63.5])

class InputActivity(BaseModel):
    title: str = Field(..., examples=["Tummy Time Reach"])
    description: str = Field(..., examples=["Place baby on tummy and hold a bright toy just out of reach."])
    image_url: Optional[str] = Field(None, description="Optional existing image URL for this Day 1 activity")

class CarePlanRequest(BaseModel):
    mode: str = Field(..., description="LEARN | MONITOR | CONCERN", examples=["MONITOR"])
    age_months: int = Field(..., ge=0, le=216, examples=[2])
    domain1: DomainScore
    domain2: DomainScore
    domain3: DomainScore
    day1_activities: list[InputActivity] = Field(
        ..., min_length=3, max_length=3,
        description="Exactly 3 Day 1 activities — caller provides these from the existing activity matrix"
    )

# ─── Response Models ────────────────────────────────────────────────────────

class Activity(BaseModel):
    title: str
    description: str
    duration: str
    steps: list[str]
    tip: str
    image_url: Optional[str] = None
    image_keywords: list[str]
    domain_focus: str
    is_priority: bool = Field(description="True when this activity targets a domain with Z-score < 65")

class DayPlan(BaseModel):
    day: int
    theme: str
    activities: list[Activity]

class CarePlanResponse(BaseModel):
    child_summary: str
    focus_domains: list[str]
    plan: list[DayPlan]
    general_tips: list[str]

# ─── JSON Schema (compatible with both OpenAI strict mode and Gemini) ────────
# OpenAI strict mode requires: additionalProperties=false, all keys in required
# Gemini ignores additionalProperties — schema works for both

_ACTIVITY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title":          {"type": "string"},
        "description":    {"type": "string"},
        "duration":       {"type": "string"},
        "steps":          {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 5},
        "tip":            {"type": "string"},
        "image_keywords": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4},
        "domain_focus":   {"type": "string"},
        "is_priority":    {"type": "boolean"},
    },
    "required": ["title", "description", "duration", "steps", "tip", "image_keywords", "domain_focus", "is_priority"],
}

_DAY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "day":        {"type": "integer"},
        "theme":      {"type": "string"},
        "activities": {"type": "array", "items": _ACTIVITY_SCHEMA, "minItems": 3, "maxItems": 3},
    },
    "required": ["day", "theme", "activities"],
}

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "child_summary":  {"type": "string"},
        "focus_domains":  {"type": "array", "items": {"type": "string"}},
        "days":           {"type": "array", "items": _DAY_SCHEMA, "minItems": 6, "maxItems": 6},
        "general_tips":   {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 5},
    },
    "required": ["child_summary", "focus_domains", "days", "general_tips"],
}

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _age_label(age_months: int) -> str:
    if age_months < 24:
        return f"{age_months} months (infant/toddler)"
    y, m = divmod(age_months, 12)
    return f"{y} years {m} months" if m else f"{y} years"

def _classify_domains(req: CarePlanRequest) -> tuple[list[DomainScore], list[DomainScore]]:
    priority, on_track = [], []
    for d in (req.domain1, req.domain2, req.domain3):
        (priority if d.zscore < ZSCORE_THRESHOLD else on_track).append(d)
    return priority, on_track

def _build_prompt(req: CarePlanRequest, priority: list[DomainScore]) -> str:
    mode_key = req.mode.upper()
    domains_block = "\n".join(
        f"  • {d.name}: Z-score = {d.zscore:.1f}  "
        f"{'⚠ NEEDS IMPROVEMENT (< 65)' if d.zscore < ZSCORE_THRESHOLD else '✓ On-Track'}"
        for d in (req.domain1, req.domain2, req.domain3)
    )
    day1_block = "\n".join(
        f"  {i+1}. {a.title}: {a.description}"
        for i, a in enumerate(req.day1_activities)
    )
    priority_names = [d.name for d in priority]
    focus_note = (
        f"PRIORITY DOMAINS (dedicate ≥ 2 activities per day to these): {', '.join(priority_names)}"
        if priority_names
        else "All domains are on-track — create enriching activities spread evenly across all 3 domains."
    )

    return f"""You are a specialist child development therapist creating a personalised 7-day home activity plan.

CHILD PROFILE
─────────────
Age            : {_age_label(req.age_months)}
Mode           : {mode_key} — {MODE_CONTEXT.get(mode_key, mode_key)}

DOMAIN Z-SCORES  (Z-score < 65 = needs improvement)
────────────────────────────────────────────────────
{domains_block}

{focus_note}

DAY 1 ACTIVITIES — provided by parent (use as difficulty & style reference for progression)
───────────────────────────────────────────────────────────────────────────────────────────
{day1_block}

YOUR TASK: Generate DAYS 2 through 7 (exactly 6 days, exactly 3 activities each day).

STRICT RULES
────────────
1. Each activity max 1–2 minutes; parent-led; no specialist equipment (home items only).
2. Steps must be CONCRETE numbered instructions a parent can follow RIGHT NOW:
   BAD : "Encourage your baby to look at the toy."
   GOOD: "Hold the rattle 30 cm from baby's face; slowly move it left 15 cm then right 15 cm."
3. Gradually increase difficulty from Day 2 → Day 7.
4. No activity may repeat across days — vary the title, method, and prop used.
5. Each day gets a short motivating theme (e.g. "Stretch & Discover", "Sensory Play Time").
6. image_keywords: 2–4 concise search words to find a photo of this activity (e.g. "baby tummy time mat").
7. tip: one plain-English sentence explaining the developmental WHY — no clinical jargon.
8. domain_focus: must exactly match one of the three domain names listed above.
9. is_priority: true if domain_focus is a priority domain (Z-score < 65), else false.
10. Calibrate all language and complexity for a child aged {_age_label(req.age_months)}.

Return valid JSON only — no markdown, no explanation, just the JSON object."""

async def _fetch_unsplash(keywords: list[str]) -> Optional[str]:
    key = os.getenv("UNSPLASH_ACCESS_KEY")
    if not key:
        return None
    query = " ".join(keywords[:2])
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                "https://api.unsplash.com/search/photos",
                params={"query": query, "per_page": 1, "orientation": "landscape"},
                headers={"Authorization": f"Client-ID {key}"},
            )
            if r.status_code == 200:
                results = r.json().get("results", [])
                if results:
                    return results[0]["urls"]["small"]
    except Exception:
        pass
    return None

# ─── Core generator ──────────────────────────────────────────────────────────

async def _generate_plan(req: CarePlanRequest) -> CarePlanResponse:
    priority, _ = _classify_domains(req)
    prompt = _build_prompt(req, priority)

    # Run blocking LLM call in a thread so it doesn't block the async event loop
    try:
        data = await asyncio.to_thread(call_llm, prompt, PLAN_SCHEMA)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=502, detail=str(e))

    priority_names = {d.name for d in priority}

    # ── Day 1: assembled from caller input ──────────────────────────────────
    domains_ordered = [req.domain1, req.domain2, req.domain3]
    day1_activities: list[Activity] = []
    for i, act in enumerate(req.day1_activities):
        domain = domains_ordered[i % 3]
        day1_activities.append(Activity(
            title=act.title,
            description=act.description,
            duration="~1 minute",
            steps=[act.description],
            tip="Observe how your child responds and note any new reactions today.",
            image_url=act.image_url,
            image_keywords=[w.lower() for w in act.title.split()[:3]],
            domain_focus=domain.name,
            is_priority=domain.name in priority_names,
        ))
    day1 = DayPlan(day=1, theme="Introduction Day", activities=day1_activities)

    # ── Days 2–7: assembled from LLM output ─────────────────────────────────
    llm_days: list[DayPlan] = []
    for day_data in data.get("days", []):
        activities: list[Activity] = []
        for act_data in day_data.get("activities", []):
            image_url = await _fetch_unsplash(act_data.get("image_keywords", []))
            activities.append(Activity(
                title=act_data["title"],
                description=act_data["description"],
                duration=act_data["duration"],
                steps=act_data["steps"],
                tip=act_data["tip"],
                image_url=image_url,
                image_keywords=act_data.get("image_keywords", []),
                domain_focus=act_data["domain_focus"],
                is_priority=act_data.get("is_priority", False),
            ))
        llm_days.append(DayPlan(
            day=day_data["day"],
            theme=day_data.get("theme", f"Day {day_data['day']}"),
            activities=activities,
        ))

    return CarePlanResponse(
        child_summary=data.get("child_summary", ""),
        focus_domains=data.get("focus_domains", []),
        plan=[day1] + llm_days,
        general_tips=data.get("general_tips", []),
    )

# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/health", tags=["Utility"])
def health_check():
    """Quick liveness check — also shows which LLM provider is active."""
    return {
        "status": "ok",
        "service": "Neev Care Planner API",
        "llm_provider": LLM_PROVIDER,
        "model": OPENAI_MODEL if LLM_PROVIDER == "openai" else GEMINI_MODEL,
    }

@app.post("/care-plan", response_model=CarePlanResponse, tags=["Care Plan"])
async def create_care_plan(req: CarePlanRequest):
    """
    Generate a 7-day activity plan for a child.

    - **Day 1** is built directly from your `day1_activities` input.
    - **Days 2–7** are generated by the configured LLM (OpenAI or Gemini).
    - Domains with Z-score **< 65** receive priority focus (≥ 2 activities per day).
    - Each activity includes: title, description, numbered steps, duration, tip, and image URL.
    - Switch LLM via `LLM_PROVIDER` env var: `openai` (default) or `gemini`.
    """
    try:
        return await _generate_plan(req)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
