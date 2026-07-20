from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class ProjectStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


class ChapterStatus(StrEnum):
    PLANNED = "PLANNED"
    RESEARCHING = "RESEARCHING"
    WRITING = "WRITING"
    REVIEWING = "REVIEWING"
    REVISING = "REVISING"
    READY = "READY"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


class IssueSeverity(StrEnum):
    MINOR = "MINOR"
    MAJOR = "MAJOR"
    BLOCKER = "BLOCKER"


class ReviewDecision(StrEnum):
    PASS = "PASS"
    REVISE = "REVISE"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NovelCreateRequest(StrictModel):
    preferred_title: str | None = Field(default=None, max_length=80)
    audience_channel: str = "男频"
    genre: str = Field(min_length=1, max_length=40)
    experiences: list[str] = Field(default_factory=list, max_length=8)
    elements: list[str] = Field(default_factory=list, max_length=12)
    protagonist_tags: list[str] = Field(default_factory=list, max_length=8)
    romance: str | None = Field(default=None, max_length=40)
    idea: str = Field(min_length=1, max_length=2000)
    must_have: list[str] = Field(default_factory=list, max_length=20)
    nice_to_have: list[str] = Field(default_factory=list, max_length=20)
    exclude: list[str] = Field(default_factory=list, max_length=20)
    chapter_target_chars: int = Field(default=2200, ge=800, le=8000)
    daily_chapters: int = Field(default=2, ge=1, le=20)
    reserve_target: int = Field(default=5, ge=1, le=50)
    require_plan_confirmation: bool = True

    @field_validator("genre", "idea", "preferred_title", mode="before")
    @classmethod
    def strip_required(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("audience_channel")
    @classmethod
    def validate_audience_channel(cls, value: str) -> str:
        if value not in {"男频", "女频"}:
            raise ValueError("频道只能选择男频或女频")
        return value


class BriefRegenerateRequest(StrictModel):
    feedback: str = Field(default="", max_length=2000)
    sections: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("feedback", mode="before")
    @classmethod
    def strip_feedback(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("sections")
    @classmethod
    def validate_sections(cls, value: list[str]) -> list[str]:
        allowed = {
            "title",
            "story",
            "protagonist",
            "world",
            "opening",
            "style",
            "serialization",
        }
        invalid = sorted(set(value) - allowed)
        if invalid:
            raise ValueError(f"不支持的方案模块：{'、'.join(invalid)}")
        return list(dict.fromkeys(value))


class CharacterProfile(StrictModel):
    name: str = Field(min_length=1, max_length=20)
    role: str = Field(default="supporting", max_length=30)
    importance: Literal["core", "major"] = "major"
    profile: str = Field(default="", max_length=1000)
    personality: list[str] = Field(default_factory=list, max_length=8)
    core_goal: str = Field(default="", max_length=500)
    relationships: dict[str, str] = Field(default_factory=dict)

    @field_validator("name", "role", "profile", "core_goal", mode="before")
    @classmethod
    def strip_character_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class CreativeBrief(StrictModel):
    title_candidates: list[str]
    selected_title: str
    synopsis: str
    target_readers: str
    selling_points: list[str]
    protagonist: str
    protagonist_name: str = ""
    protagonist_profile: str = ""
    character_profiles: list[CharacterProfile] = Field(default_factory=list, max_length=20)
    main_conflict: str
    world_rules: list[str]
    total_chapters: int
    volume_count: int
    opening_three_chapters: list[str]
    update_plan: str
    style_guide: list[str]
    reader_contract: str = ""
    core_expectation: str = ""

    @model_validator(mode="after")
    def normalize_protagonist(self) -> "CreativeBrief":
        """Keep legacy briefs compatible while separating a name from a biography."""
        raw = (self.protagonist_profile or self.protagonist or "").strip()
        candidate = (self.protagonist_name or self.protagonist or "").strip()
        candidate = re.sub(r"^主角(?:姓名)?\s*[：:]?\s*", "", candidate)
        name = re.split(r"[，,：:；;（(\n]", candidate, maxsplit=1)[0].strip()
        if not name:
            raise ValueError("创作方案缺少主角姓名")
        if len(name) > 20:
            raise ValueError("主角姓名过长，请把人物介绍放入 protagonist_profile")
        self.protagonist_name = name
        self.protagonist_profile = raw if raw != name else self.protagonist_profile.strip()
        # `protagonist` remains the compatibility field used by older clients.
        self.protagonist = name
        profiles: list[CharacterProfile] = []
        seen: set[str] = set()
        for profile in self.character_profiles:
            if profile.name in seen:
                continue
            seen.add(profile.name)
            if profile.name == name:
                profile = profile.model_copy(
                    update={
                        "role": "protagonist",
                        "importance": "core",
                        "profile": profile.profile or self.protagonist_profile,
                        "core_goal": profile.core_goal or self.main_conflict,
                    }
                )
            elif profile.role == "protagonist":
                profile = profile.model_copy(update={"role": "supporting"})
            profiles.append(profile)
        if name not in seen:
            profiles.insert(
                0,
                CharacterProfile(
                    name=name,
                    role="protagonist",
                    importance="core",
                    profile=self.protagonist_profile,
                    core_goal=self.main_conflict,
                ),
            )
        self.character_profiles = profiles
        return self


class CharacterMemoryUpdate(StrictModel):
    name: str = Field(min_length=1, max_length=20)
    role: str = Field(default="supporting", max_length=30)
    importance: Literal["core", "major"] = "major"
    profile: str = Field(default="", max_length=1000)
    immutable_facts: list[str] = Field(default_factory=list, max_length=12)
    current_goal: str = Field(default="", max_length=500)
    last_result: str = Field(default="", max_length=500)
    location: str = Field(default="", max_length=200)
    knowledge_gained: list[str] = Field(default_factory=list, max_length=12)
    relationship_changes: dict[str, str] = Field(default_factory=dict)

    @field_validator("name", "role", "profile", "current_goal", "last_result", "location", mode="before")
    @classmethod
    def strip_memory_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class CharacterMemoryBatch(StrictModel):
    characters: list[CharacterMemoryUpdate] = Field(default_factory=list, max_length=30)


class SceneBeat(StrictModel):
    sequence: int = Field(ge=1)
    scene: str = Field(min_length=1, max_length=1000)
    purpose: str = Field(min_length=1, max_length=300)
    emotional_shift: str = Field(min_length=1, max_length=300)
    intensity: Literal["dense", "normal", "sparse"] = "normal"
    target_chars: int = Field(ge=40, le=3000)


class ChapterPlan(StrictModel):
    number: int
    title: str
    objective: str
    conflict: str
    turning_point: str
    hook: str
    scenes: list[str]
    research_questions: list[str] = Field(default_factory=list)
    character_updates: list[CharacterMemoryUpdate] = Field(default_factory=list, max_length=20)
    story_stage: str = ""
    plot_unit: str = ""
    chapter_position: str = "推进"
    target_emotion: str = ""
    reader_payoff: str = ""
    new_expectation: str = ""
    protagonist_goal: str = ""
    critical_choice: str = ""
    opening_hook: str = ""
    forbidden_releases: list[str] = Field(default_factory=list, max_length=12)
    plotline_progress: dict[str, str] = Field(default_factory=dict)
    relationship_changes: dict[str, str] = Field(default_factory=dict)
    information_gap: list[str] = Field(default_factory=list, max_length=12)
    scene_beats: list[SceneBeat] = Field(default_factory=list, max_length=20)


class ProseFinding(StrictModel):
    code: str
    severity: IssueSeverity
    message: str
    suggestion: str
    evidence: str = ""


class ProseAuditReport(StrictModel):
    level: Literal["clean", "light", "medium", "heavy"] = "clean"
    score: float = Field(default=100, ge=0, le=100)
    findings: list[ProseFinding] = Field(default_factory=list, max_length=100)
    metrics: dict[str, float] = Field(default_factory=dict)


class ReviewIssue(StrictModel):
    category: str
    severity: IssueSeverity
    message: str
    suggestion: str
    evidence: list[str] = Field(default_factory=list, max_length=12)


class ReviewReport(StrictModel):
    decision: ReviewDecision
    score: float = Field(ge=0, le=100)
    issues: list[ReviewIssue] = Field(default_factory=list)
    checks: dict[str, float] = Field(default_factory=dict)


class ContinuityContext(StrictModel):
    """Structured runtime input for the vendored Story Skills audit."""

    skill_name: str
    skill_source: str
    project_id: str
    chapter_number: int
    creative_request: dict[str, Any]
    creative_brief: dict[str, Any]
    chapter_plan: dict[str, Any]
    current_draft: str
    recent_chapters: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    characters: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    world_facts: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    timeline: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    foreshadows: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    deterministic_findings: list[ReviewIssue] = Field(default_factory=list, max_length=100)


class CharacterRecord(StrictModel):
    id: str
    project_id: str
    name: str
    role: str
    immutable_facts: list[str]
    current_state: dict[str, Any]
    version: int = 1


class WorldFactRecord(StrictModel):
    id: str
    project_id: str
    category: str
    statement: str
    evidence_chapter: int | None = None
    version: int = 1
    locked: bool = False


class ChapterRecord(StrictModel):
    id: str
    project_id: str
    number: int
    title: str
    status: ChapterStatus
    plan: ChapterPlan | None = None
    content: str | None = None
    summary: str | None = None
    version: int = 0
    locked: bool = False
    created_at: str
    updated_at: str


class ProjectRecord(StrictModel):
    id: str
    title: str
    status: ProjectStatus
    request: NovelCreateRequest
    brief: CreativeBrief
    confirmed: bool
    current_chapter: int
    created_at: str
    updated_at: str


class RunEvent(StrictModel):
    id: int | None = None
    run_id: str
    project_id: str
    chapter_id: str | None = None
    node: str
    message: str
    progress: int = Field(ge=0, le=100)
    level: str = "INFO"
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)


class WorkflowResult(StrictModel):
    run_id: str
    chapter: ChapterRecord
    review: ReviewReport
    events: list[RunEvent]
