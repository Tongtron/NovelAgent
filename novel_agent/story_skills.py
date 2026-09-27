from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from novel_agent.models import (
    ChapterPlan,
    ChapterStatus,
    ContinuityContext,
    IssueSeverity,
    ProjectRecord,
    ReviewDecision,
    ReviewIssue,
    ReviewReport,
)

if TYPE_CHECKING:
    from novel_agent.adapters import LLMGateway
    from novel_agent.repository import SQLiteRepository


STORY_SKILLS_REPOSITORY = "danjdewhurst/story-skills"
STORY_SKILLS_COMMIT = "c482d48f4eb9b488f033a77a51f9fae55cc0d75f"
REQUIRED_SKILLS = ("revision-continuity", "story-maintenance")


class StorySkillsConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StorySkillDefinition:
    name: str
    description: str
    instructions: str
    source_path: Path


@dataclass(slots=True)
class StorySkillsRuntime:
    """NovelAgent-native adapter for the vendored Story Skills rules.

    SQLite remains authoritative. The bundled Node CLI targets Story Skills'
    markdown schema, so the runtime applies its deterministic contracts in
    Python and supplies the approved revision skill instructions to the LLM.
    """

    enabled: bool = False
    source_root: Path | None = None
    definitions: dict[str, StorySkillDefinition] = field(default_factory=dict)
    source_commit: str = STORY_SKILLS_COMMIT

    @classmethod
    def disabled(cls) -> "StorySkillsRuntime":
        return cls(enabled=False)

    @classmethod
    def load(cls, project_root: Path, *, enabled: bool = True) -> "StorySkillsRuntime":
        source_root = (
            project_root / "vendor" / "story-skills" / "skills"
        ).resolve()
        definitions: dict[str, StorySkillDefinition] = {}
        missing: list[str] = []
        for name in REQUIRED_SKILLS:
            path = source_root / name / "SKILL.md"
            if not path.is_file():
                missing.append(str(path))
                continue
            definition = cls._read_definition(path)
            if definition.name != name:
                raise StorySkillsConfigurationError(
                    f"Story Skills 名称不匹配：期望 {name}，实际 {definition.name}"
                )
            definitions[name] = definition
        if missing and enabled:
            raise StorySkillsConfigurationError(
                "Story Skills 已启用但缺少固定文件：" + "；".join(missing)
            )
        return cls(
            enabled=enabled and not missing,
            source_root=source_root,
            definitions=definitions,
        )

    @staticmethod
    def _read_definition(path: Path) -> StorySkillDefinition:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise StorySkillsConfigurationError(f"Skill 缺少有效 frontmatter：{path}")
        header, instructions = text[4:].split("\n---\n", 1)
        metadata: dict[str, str] = {}
        for raw_line in header.splitlines():
            if ":" not in raw_line:
                continue
            key, value = raw_line.split(":", 1)
            metadata[key.strip()] = value.strip()
        name = metadata.get("name", "")
        description = metadata.get("description", "")
        if not name or not description or not instructions.strip():
            raise StorySkillsConfigurationError(f"Skill 元数据不完整：{path}")
        return StorySkillDefinition(
            name=name,
            description=description,
            instructions=instructions.strip(),
            source_path=path,
        )

    def readiness(self) -> dict[str, object]:
        return {
            "provider": STORY_SKILLS_REPOSITORY,
            "configured": all(name in self.definitions for name in REQUIRED_SKILLS),
            "enabled": self.enabled,
            "version": self.source_commit[:12],
            "skills": list(self.definitions),
        }

    def audit(
        self,
        repository: "SQLiteRepository",
        gateway: "LLMGateway",
        project: ProjectRecord,
        plan: ChapterPlan,
        content: str,
    ) -> ReviewReport:
        if not self.enabled:
            return ReviewReport(
                decision=ReviewDecision.PASS,
                score=100,
                checks={"story_skills_enabled": 0},
            )
        definition = self.definitions["revision-continuity"]
        deterministic = self._deterministic_findings(repository, project, plan, content)
        context = self._build_context(
            repository,
            project,
            plan,
            content,
            deterministic,
        )
        semantic = gateway.review_continuity(context, definition.instructions)
        return self._merge_story_reports(deterministic, semantic)

    def _build_context(
        self,
        repository: "SQLiteRepository",
        project: ProjectRecord,
        plan: ChapterPlan,
        content: str,
        deterministic: list[ReviewIssue],
    ) -> ContinuityContext:
        previous = [
            item
            for item in repository.list_chapters(project.id)
            if item.number < plan.number and item.status == ChapterStatus.READY
        ][-6:]
        recent_chapters = [
            {
                "number": item.number,
                "title": item.title,
                "summary": item.summary or "",
                "ending_excerpt": (item.content or "")[-1200:],
            }
            for item in previous
        ]
        return ContinuityContext(
            skill_name="revision-continuity",
            skill_source=f"{STORY_SKILLS_REPOSITORY}@{self.source_commit}",
            project_id=project.id,
            chapter_number=plan.number,
            creative_request=project.request.model_dump(mode="json"),
            creative_brief=project.brief.model_dump(mode="json"),
            chapter_plan=plan.model_dump(mode="json"),
            current_draft=content,
            recent_chapters=recent_chapters,
            characters=[
                item.model_dump(mode="json")
                for item in repository.list_characters(project.id)
            ],
            world_facts=[
                item.model_dump(mode="json")
                for item in repository.list_world_facts(project.id)
            ],
            timeline=repository.list_timeline(project.id)[-40:],
            foreshadows=repository.list_foreshadows(project.id),
            deterministic_findings=deterministic,
        )

    @staticmethod
    def _deterministic_findings(
        repository: "SQLiteRepository",
        project: ProjectRecord,
        plan: ChapterPlan,
        content: str,
    ) -> list[ReviewIssue]:
        issues: list[ReviewIssue] = []
        timeline = repository.list_timeline(project.id)
        future_events = [
            item for item in timeline if int(item["chapter_number"]) >= plan.number
        ]
        if future_events:
            issues.append(
                ReviewIssue(
                    category="timeline",
                    severity=IssueSeverity.BLOCKER,
                    message="正式时间线包含当前章节或未来章节的预写事件",
                    suggestion="删除或更正尚未正式发生的时间线事件后重新审核",
                    evidence=[str(item.get("event", "")) for item in future_events[:5]],
                )
            )

        active_names = {item.name for item in plan.character_updates}
        deceased_markers = {"dead", "deceased", "死亡", "已死亡", "已故"}
        for character in repository.list_characters(project.id):
            state = character.current_state
            status = str(state.get("status") or state.get("life_status") or "").lower()
            if status not in deceased_markers or character.name not in content:
                continue
            severity = (
                IssueSeverity.BLOCKER
                if character.name in active_names
                else IssueSeverity.MINOR
            )
            issues.append(
                ReviewIssue(
                    category="character_state",
                    severity=severity,
                    message=f"已故人物“{character.name}”在本章正文中出现",
                    suggestion=(
                        "若为回忆、记录或他人提及，请改成明确的非现场表达；"
                        "若人物实际存活，请先修正正式人物状态"
                    ),
                    evidence=[f"人物正式状态：{status}"],
                )
            )

        for item in repository.list_foreshadows(project.id):
            introduced = int(item["introduced_chapter"])
            resolved_raw = item.get("resolved_chapter")
            resolved = int(resolved_raw) if resolved_raw is not None else None
            status = str(item.get("status") or "OPEN").upper()
            if introduced > plan.number:
                issues.append(
                    ReviewIssue(
                        category="foreshadowing",
                        severity=IssueSeverity.BLOCKER,
                        message=f"伏笔“{item['label']}”的埋设章节晚于当前创作进度",
                        suggestion="修正伏笔埋设章节或移除尚未发生的正式伏笔记录",
                        evidence=[f"introduced={introduced}", f"current={plan.number}"],
                    )
                )
            if resolved is not None and resolved < introduced:
                issues.append(
                    ReviewIssue(
                        category="foreshadowing",
                        severity=IssueSeverity.BLOCKER,
                        message=f"伏笔“{item['label']}”在埋设之前已经回收",
                        suggestion="更正伏笔的埋设或回收章节顺序",
                        evidence=[f"introduced={introduced}", f"resolved={resolved}"],
                    )
                )
            if status == "RESOLVED" and resolved is None:
                issues.append(
                    ReviewIssue(
                        category="foreshadowing",
                        severity=IssueSeverity.MAJOR,
                        message=f"伏笔“{item['label']}”标记为已回收但缺少回收章节",
                        suggestion="记录实际回收章节，或把伏笔状态恢复为 OPEN",
                    )
                )
        return issues

    @staticmethod
    def _merge_story_reports(
        deterministic: list[ReviewIssue],
        semantic: ReviewReport,
    ) -> ReviewReport:
        issues: list[ReviewIssue] = []
        seen: set[tuple[str, str]] = set()
        for issue in [*deterministic, *semantic.issues]:
            key = (issue.category, issue.message)
            if key in seen:
                continue
            seen.add(key)
            issues.append(issue)

        blocker_count = sum(item.severity == IssueSeverity.BLOCKER for item in issues)
        major_count = sum(item.severity == IssueSeverity.MAJOR for item in issues)
        minor_count = sum(item.severity == IssueSeverity.MINOR for item in issues)
        deterministic_score = max(
            0.0,
            100.0 - blocker_count * 40 - major_count * 18 - minor_count * 3,
        )
        score = min(float(semantic.score), deterministic_score)
        if semantic.decision == ReviewDecision.HUMAN_REQUIRED:
            decision = ReviewDecision.HUMAN_REQUIRED
        elif blocker_count or major_count or score < 85:
            decision = ReviewDecision.REVISE
        else:
            decision = ReviewDecision.PASS
        checks = {
            "story_skills_deterministic": deterministic_score,
            "story_skills_semantic": float(semantic.score),
        }
        checks.update(
            {
                key if key.startswith("story_skills_") else f"story_skills_{key}": value
                for key, value in semantic.checks.items()
            }
        )
        return ReviewReport(
            decision=decision,
            score=score,
            issues=issues,
            checks=checks,
        )

