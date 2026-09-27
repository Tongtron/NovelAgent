from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from novel_agent.adapters import (
    DisabledEmbeddingGateway,
    EmbeddingGateway,
    LLMGateway,
    SearchAdapter,
)
from novel_agent.memory_manager import MemoryManager
from novel_agent.models import (
    ChapterPlan,
    ChapterRecord,
    ChapterStatus,
    CharacterMemoryUpdate,
    IssueSeverity,
    ProjectRecord,
    ProjectStatus,
    ReadabilityContext,
    ReviewDecision,
    ReviewIssue,
    ReviewReport,
    RunEvent,
    RunStatus,
    WorkflowResult,
    new_id,
    utc_now,
)
from novel_agent.repository import SQLiteRepository
from novel_agent.story_skills import StorySkillsRuntime
from novel_agent.writing_skills import OhStoryWritingRuntime


class WorkflowBlocked(RuntimeError):
    pass


QUALITY_PASS_SCORE = 60.0


@dataclass(slots=True)
class ChapterWorkflow:
    repository: SQLiteRepository
    gateway: LLMGateway
    search: SearchAdapter
    max_revision_attempts: int = 2
    embedding: EmbeddingGateway = field(default_factory=DisabledEmbeddingGateway)
    semantic_retrieval_limit: int = 6
    story_skills: StorySkillsRuntime = field(default_factory=StorySkillsRuntime.disabled)
    writing_skills: OhStoryWritingRuntime = field(default_factory=OhStoryWritingRuntime.disabled)
    memory_manager: MemoryManager = field(default_factory=MemoryManager)
    _events: list[RunEvent] = field(default_factory=list, init=False)

    def run(self, project_id: str) -> WorkflowResult:
        self._events = []
        project = self.repository.get_project(project_id)
        self._assert_runnable(project)
        number = project.current_chapter + 1
        existing = self.repository.get_chapter_by_number(project_id, number)
        if existing and existing.status == ChapterStatus.READY:
            report = ReviewReport(decision=ReviewDecision.PASS, score=100, checks={"idempotent": 100})
            return WorkflowResult(run_id="already_ready", chapter=existing, review=report, events=[])

        chapter = existing or ChapterRecord(
            id=new_id("chapter"),
            project_id=project_id,
            number=number,
            title=f"第{number}章 待规划",
            status=ChapterStatus.PLANNED,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        if existing is None:
            chapter = self.repository.create_chapter(chapter)
        run_id = new_id("run")
        self.repository.create_run(run_id, project_id, chapter.id)

        try:
            self._emit(run_id, project_id, chapter.id, "LoadContext", "加载最近章节与正式记忆", 5)
            chapters = self.repository.list_chapters(project_id)
            summaries = [item.summary for item in chapters[-6:] if item.summary]
            previous_ready = [
                item
                for item in chapters
                if item.number < number
                and item.status == ChapterStatus.READY
                and item.content
            ]
            previous_chapter_excerpt = (
                (previous_ready[-1].content or "")[-1800:]
                if previous_ready
                else ""
            )
            timeline = self.repository.list_timeline(project_id)
            character_roster = self.repository.list_characters(project_id)
            open_threads = [item["event"][:40] for item in timeline[-3:]] or ["异常来源"]
            semantic_research_notes: list[str] = []
            if self.embedding.enabled:
                self._index_pending(run_id, project_id, chapter.id, progress=8)
                try:
                    query = "；".join(
                        [project.brief.main_conflict, *summaries[-2:], *open_threads[-3:]]
                    )
                    vector = self.embedding.embed([query])[0]
                    matches = self.repository.semantic_search(
                        project_id, vector, self.semantic_retrieval_limit
                    )
                    for match in matches:
                        recalled = str(match["content"])[:1200]
                        if match["source_type"] == "chapter_summary":
                            if recalled not in summaries:
                                summaries.append(recalled)
                        else:
                            metadata = match.get("metadata", {})
                            semantic_research_notes.append(
                                self._research_note(
                                    str(metadata.get("title") or "历史研究资料"),
                                    str(metadata.get("url") or ""),
                                    recalled,
                                    float(match["score"]),
                                    True,
                                )
                            )
                    self._emit(
                        run_id,
                        project_id,
                        chapter.id,
                        "SemanticRecall",
                        f"从章节摘要与研究资料中召回 {len(matches)} 条相关记忆",
                        10,
                        payload={"matches": len(matches)},
                    )
                except Exception as exc:
                    self._emit(
                        run_id,
                        project_id,
                        chapter.id,
                        "SemanticRecall",
                        f"语义检索暂不可用，已降级为最近章节上下文：{type(exc).__name__}",
                        10,
                        level="WARN",
                    )

            writing_guidance = self.writing_skills.chapter_guidance(
                project.request.genre,
                project.request.audience_channel,
            )
            if writing_guidance:
                self._emit(
                    run_id,
                    project_id,
                    chapter.id,
                    "GenreProseCard",
                    f"加载题材正文提示卡：{writing_guidance['genre_card_name']}",
                    13,
                    payload={
                        "skill": writing_guidance["skill"],
                        "source": writing_guidance["source"],
                        "genre_card": writing_guidance["genre_card_name"],
                        "confidence": writing_guidance["genre_card_confidence"],
                    },
                )
            rework_feedback_raw = self.repository.get_setting(
                f"rework_feedback:{chapter.id}"
            )
            rework_feedback = (
                str(rework_feedback_raw).strip()
                if rework_feedback_raw
                else ""
            )
            if rework_feedback:
                self.repository.delete_setting(f"rework_feedback:{chapter.id}")
                writing_guidance = writing_guidance or {}
                writing_guidance["rework_feedback"] = rework_feedback
                self._emit(
                    run_id, project_id, chapter.id,
                    "ReworkFeedback",
                    f"本章打回重做，修改意见：{rework_feedback[:80]}",
                    14,
                )
            research_sources = self.repository.list_research_sources(project_id)
            chapter_id_to_number = {ch.id: ch.number for ch in chapters}
            for rs in research_sources:
                ch_id = rs.get("chapter_id")
                rs["_chapter_number"] = chapter_id_to_number.get(ch_id)
            compact_ctx = self.memory_manager.build_context(
                characters=character_roster,
                world_facts=self.repository.list_world_facts(project_id),
                timeline=timeline,
                research_sources=research_sources,
                current_chapter=number,
            )
            if existing and existing.plan is not None and rework_feedback:
                plan = existing.plan
                self._emit(
                    run_id,
                    project_id,
                    chapter.id,
                    "PlanReuse",
                    "沿用上次细纲，按审稿意见重新写作，避免重复规划消耗",
                    20,
                )
            else:
                self._emit(
                    run_id,
                    project_id,
                    chapter.id,
                    "Plan",
                    "生成一份包含冲突、选择、代价与情节点预算的章节细纲",
                    15,
                )
                plan = self.gateway.plan_chapter(
                    project.request,
                    project.brief,
                    number,
                    summaries,
                    open_threads,
                    character_roster,
                    writing_guidance,
                    compact_characters=compact_ctx.get("compact_characters"),
                    compact_world_facts=compact_ctx.get("compact_world_facts"),
                    compact_timeline=compact_ctx.get("compact_timeline"),
                    previous_chapter_excerpt=previous_chapter_excerpt,
                )
                plan = self.writing_skills.normalize_plan(
                    plan,
                    total_chapters=project.brief.total_chapters,
                    target_chars=project.request.chapter_target_chars,
                )
            chapter = self.repository.update_chapter(
                chapter.id, status=ChapterStatus.PLANNED, plan=plan, title=plan.title
            )

            research_notes: list[str] = [
                *compact_ctx.get("compact_research", []),
                *semantic_research_notes,
            ]
            if plan.research_questions:
                chapter = self.repository.update_chapter(
                    chapter.id, status=ChapterStatus.RESEARCHING
                )
                if self.search.enabled:
                    self._emit(run_id, project_id, chapter.id, "Research", "执行外部知识调研", 28)
                    try:
                        sources = self.search.research(plan.research_questions)
                        saved_sources = self.repository.save_research_sources(
                            project_id, chapter.id, sources
                        )
                        research_notes.extend(
                            self._research_note(
                                str(source.get("title") or "网页来源"),
                                str(source.get("url") or ""),
                                str(source.get("excerpt") or source.get("fact") or ""),
                                float(source.get("reliability", 0.0)),
                                bool(source.get("verified")),
                            )
                            for source in saved_sources
                            if source.get("excerpt") or source.get("fact")
                        )
                        self._emit(
                            run_id,
                            project_id,
                            chapter.id,
                            "SourceVerification",
                            f"保存 {len(saved_sources)} 条网页来源并完成可信度与交叉来源标记",
                            34,
                            payload={"sources": len(saved_sources)},
                        )
                        if self.embedding.enabled:
                            self._index_pending(run_id, project_id, chapter.id, progress=36)
                    except Exception as exc:
                        self._emit(
                            run_id,
                            project_id,
                            chapter.id,
                            "ResearchFallback",
                            f"联网调研失败，改用保守写法：{type(exc).__name__}",
                            34,
                            level="WARN",
                        )
                else:
                    self._emit(
                        run_id,
                        project_id,
                        chapter.id,
                        "ResearchDecision",
                        "检测到研究需求，但搜索 API 未启用；使用保守写法并标记边界",
                        28,
                        level="WARN",
                        payload={"questions": plan.research_questions},
                    )
            else:
                self._emit(run_id, project_id, chapter.id, "ResearchDecision", "本章无需外部调研", 28)

            chapter = self.repository.update_chapter(chapter.id, status=ChapterStatus.WRITING)
            self._emit(run_id, project_id, chapter.id, "Writer", "按情绪细纲与题材提示卡生成章节草稿", 42)
            if not character_roster:
                raise WorkflowBlocked("项目缺少人物档案，请先确认创作方案或重建人物数据")
            protagonist = next(
                (item for item in character_roster if item.role == "protagonist"),
                character_roster[0],
            )
            if rework_feedback:
                research_notes = [
                    f"[打回重做修改意见] {rework_feedback}",
                    *research_notes,
                ]
            content = self.gateway.write_chapter(
                project.request,
                project.brief,
                plan,
                protagonist.name,
                research_notes,
                character_roster,
                writing_guidance.get("genre_prose_card", ""),
                compact_characters=compact_ctx.get("compact_characters"),
                previous_chapter_excerpt=previous_chapter_excerpt,
            )
            self.repository.save_draft_version(
                chapter.id,
                content,
                {"gateway": self.gateway.name, "plan": plan.model_dump(mode="json")},
            )

            if self.writing_skills.enabled:
                before_audit = self.writing_skills.audit_prose(content)
                polished = self.gateway.deslop_chapter(
                    content,
                    plan,
                    writing_guidance.get("genre_prose_card", ""),
                    self.writing_skills.deslop_guidance(),
                )
                after_audit = (
                    self.writing_skills.audit_prose(polished)
                    if polished.strip()
                    else before_audit
                )
                before_serious = sum(
                    item.severity in {IssueSeverity.BLOCKER, IssueSeverity.MAJOR}
                    for item in before_audit.findings
                )
                after_serious = sum(
                    item.severity in {IssueSeverity.BLOCKER, IssueSeverity.MAJOR}
                    for item in after_audit.findings
                )
                accepted_polish = bool(
                    polished.strip()
                    and after_audit.score >= before_audit.score
                    and after_serious <= before_serious
                )
                if accepted_polish:
                    content = polished
                    self.repository.save_draft_version(
                        chapter.id,
                        content,
                        {
                            "stage": "story-deslop",
                            "skill_source": self.writing_skills.source_commit,
                            "before": before_audit.model_dump(mode="json"),
                            "after": after_audit.model_dump(mode="json"),
                        },
                    )
                effective_audit = after_audit if accepted_polish else before_audit
                self._emit(
                    run_id,
                    project_id,
                    chapter.id,
                    "ProsePolish",
                    (
                        ("story-deslop 润色已采用：" if accepted_polish else "story-deslop 未改善，保留原稿：")
                        + f"{before_audit.level} → {after_audit.level}，"
                        f"当前剩余 {len(effective_audit.findings)} 项"
                    ),
                    52,
                    level="WARN" if any(
                        item.severity in {IssueSeverity.BLOCKER, IssueSeverity.MAJOR}
                        for item in effective_audit.findings
                    ) else "INFO",
                    payload={
                        "skill": "story-deslop",
                        "source": self.writing_skills.source_commit,
                        "before_score": before_audit.score,
                        "after_score": after_audit.score,
                        "accepted": accepted_polish,
                        "remaining_findings": len(effective_audit.findings),
                    },
                )

            report = ReviewReport(decision=ReviewDecision.REVISE, score=0)
            # Quality is intentionally a one-shot gate.  A later user click can
            # retry the same chapter and reuse its saved plan without another
            # in-run revision loop.
            for attempt in range(1):
                chapter = self.repository.update_chapter(chapter.id, status=ChapterStatus.REVIEWING)
                self._emit(
                    run_id,
                    project_id,
                    chapter.id,
                    "ParallelReview",
                    f"执行可读性、一致性、禁用项、篇幅、重复度与文本审核（第 {attempt + 1} 次）",
                    58 + attempt * 10,
                )
                base_report = self._review(
                    project,
                    plan,
                    protagonist.name,
                    content,
                    bool(research_notes),
                )
                if self.writing_skills.enabled:
                    prose_audit = self.writing_skills.audit_prose(content)
                    report = self._merge_review_reports(
                        base_report,
                        self.writing_skills.as_review_report(prose_audit),
                    )
                else:
                    report = base_report
                readability_report = self.gateway.review_readability(
                    self._readability_context(
                        project,
                        plan,
                        content,
                        previous_chapter_excerpt,
                    )
                )
                report = self._merge_review_reports(report, readability_report)
                self._emit(
                    run_id,
                    project_id,
                    chapter.id,
                    "ReadabilityReview",
                    (
                        f"可读性主编审核：{readability_report.decision.value}"
                    ),
                    60 + attempt * 10,
                    level=(
                        "INFO"
                        if readability_report.decision == ReviewDecision.PASS
                        else "WARN"
                    ),
                    payload={
                        "decision": readability_report.decision.value,
                        "score": readability_report.score,
                        "checks": readability_report.checks,
                    },
                )
                if self.story_skills.enabled:
                    skill_report = self.story_skills.audit(
                        self.repository,
                        self.gateway,
                        project,
                        plan,
                        content,
                    )
                    report = self._merge_review_reports(report, skill_report)
                    blocker_count = sum(
                        issue.severity == IssueSeverity.BLOCKER
                        for issue in skill_report.issues
                    )
                    major_count = sum(
                        issue.severity == IssueSeverity.MAJOR
                        for issue in skill_report.issues
                    )
                    self._emit(
                        run_id,
                        project_id,
                        chapter.id,
                        "StorySkillsAudit",
                        (
                            "Story Skills 一致性审校完成："
                            f"{blocker_count} 个阻断、{major_count} 个主要问题，"
                            f"得分 {skill_report.score:.0f}"
                        ),
                        62 + attempt * 10,
                        level="WARN" if skill_report.decision != ReviewDecision.PASS else "INFO",
                        payload={
                            "skill": "revision-continuity",
                            "source": self.story_skills.source_commit,
                            "decision": skill_report.decision.value,
                            "score": skill_report.score,
                        },
                    )
                # Different reviewers may return a stale REVISE decision even
                # when their numeric score is above the active pass line.  The
                # final gate is score-based; only an explicit human blocker can
                # override it.
                if (
                    report.decision != ReviewDecision.HUMAN_REQUIRED
                    and report.score > QUALITY_PASS_SCORE
                ):
                    report = report.model_copy(update={"decision": ReviewDecision.PASS})
                if report.decision == ReviewDecision.PASS:
                    break
                if report.decision == ReviewDecision.HUMAN_REQUIRED:
                    self.repository.update_chapter(chapter.id, status=ChapterStatus.FAILED)
                    self.repository.update_project_status(project_id, ProjectStatus.HUMAN_REQUIRED)
                    self.repository.update_run(
                        run_id,
                        RunStatus.HUMAN_REQUIRED.value,
                        {"node": "ReviewDecision", "report": report.model_dump(mode="json")},
                        "REVIEW_RETRY_EXHAUSTED",
                    )
                    self._emit(
                        run_id,
                        project_id,
                        chapter.id,
                        "HumanInterrupt",
                        "自动修订未能安全通过，已保存检查点并进入异常中心",
                        85,
                        level="ERROR",
                    )
                    raise WorkflowBlocked("章节需要人工处理")
                if attempt == 0:  # the one-shot gate always stops here when not PASS
                    chapter = self.repository.update_chapter(
                        chapter.id, status=ChapterStatus.FAILED
                    )
                    feedback = "；".join(
                        issue.suggestion
                        for issue in report.issues
                        if issue.severity in {IssueSeverity.BLOCKER, IssueSeverity.MAJOR}
                    )[:2000]
                    if feedback:
                        self.repository.save_setting(
                            f"rework_feedback:{chapter.id}", feedback
                        )
                    self.repository.update_run(
                        run_id,
                        RunStatus.FAILED.value,
                        {"node": "QualityHold", "report": report.model_dump(mode="json")},
                        "REVIEW_RETRY_EXHAUSTED",
                    )
                    self._emit(
                        run_id,
                        project_id,
                        chapter.id,
                        "QualityHold",
                        "本次草稿未通过质量门槛；项目保持可用，可再次生成并沿用细纲定向重写",
                        85,
                        level="WARN",
                    )
                    return WorkflowResult(
                        run_id=run_id,
                        chapter=chapter,
                        review=report,
                        events=self._events,
                    )
                chapter = self.repository.update_chapter(chapter.id, status=ChapterStatus.REVISING)
                instructions = [
                    issue.suggestion
                    if issue.suggestion.startswith("删除禁止项：")
                    else (
                        f"[{issue.category}/{issue.severity.value}] {issue.message}；"
                        f"证据：{' | '.join(issue.evidence) or '见当前正文'}；"
                        f"修改：{issue.suggestion}"
                    )
                    for issue in report.issues
                ]
                self._emit(run_id, project_id, chapter.id, "Revision", "按审核问题自动修订", 68 + attempt * 10)
                content = self.gateway.revise_chapter(
                    content, instructions, project.request.chapter_target_chars
                )
                self.repository.save_draft_version(
                    chapter.id, content, {"revision_attempt": attempt + 1, "report": report}
                )

            self._emit(run_id, project_id, chapter.id, "Summary", "生成章节摘要与记忆变更集", 88)
            summary = self._summary(plan, content)
            timeline_event = f"第{number}章：{plan.objective}；转折：{plan.turning_point}"
            extracted_updates: list[CharacterMemoryUpdate] = []
            try:
                memory_batch = self.gateway.extract_character_memories(
                    project.brief,
                    number,
                    content,
                    character_roster,
                )
                extracted_updates = memory_batch.characters
                self._emit(
                    run_id,
                    project_id,
                    chapter.id,
                    "CharacterArchive",
                    f"识别并更新 {len(extracted_updates)} 个人物档案",
                    92,
                    payload={"characters": [item.name for item in extracted_updates]},
                )
            except Exception as exc:
                self._emit(
                    run_id,
                    project_id,
                    chapter.id,
                    "CharacterArchive",
                    f"人物提取暂不可用，已使用章节计划中的人物变更：{type(exc).__name__}",
                    92,
                    level="WARN",
                )
            character_updates = self._merge_character_updates(
                [
                    *plan.character_updates,
                    *extracted_updates,
                    CharacterMemoryUpdate(
                        name=protagonist.name,
                        role="protagonist",
                        importance="core",
                        current_goal=plan.hook,
                        last_result=plan.objective,
                    ),
                ]
            )
            self._emit(run_id, project_id, chapter.id, "MemoryCommit", "原子提交正文与正式记忆", 94)
            chapter = self.repository.commit_ready_chapter(
                chapter.id,
                run_id,
                content,
                summary,
                report,
                timeline_event,
                character_updates,
            )
            if self.embedding.enabled:
                self._index_pending(run_id, project_id, chapter.id, progress=97)
            self.repository.update_run(
                run_id,
                RunStatus.COMPLETED.value,
                {"node": "ChapterReady", "chapter_id": chapter.id, "version": chapter.version},
            )
            self._emit(run_id, project_id, chapter.id, "ChapterReady", "章节审核通过并进入 READY 储备池", 100)
            return WorkflowResult(run_id=run_id, chapter=chapter, review=report, events=self._events)
        except WorkflowBlocked:
            raise
        except Exception as exc:
            self.repository.update_chapter(chapter.id, status=ChapterStatus.FAILED)
            self.repository.update_run(
                run_id,
                RunStatus.FAILED.value,
                {"node": self._events[-1].node if self._events else "unknown"},
                type(exc).__name__,
            )
            self._emit(
                run_id,
                project_id,
                chapter.id,
                "Failure",
                f"工作流失败：{type(exc).__name__}",
                100,
                level="ERROR",
            )
            raise

    @staticmethod
    def _readability_context(
        project: ProjectRecord,
        plan: ChapterPlan,
        content: str,
        previous_chapter_excerpt: str,
    ) -> ReadabilityContext:
        request = project.request
        brief = project.brief
        return ReadabilityContext(
            project_id=project.id,
            chapter_number=plan.number,
            creative_request={
                "audience_channel": request.audience_channel,
                "genre": request.genre,
                "experiences": request.experiences,
                "elements": request.elements,
                "protagonist_tags": request.protagonist_tags,
                "romance": request.romance,
                "idea": request.idea,
                "must_have": request.must_have,
                "nice_to_have": request.nice_to_have,
                "exclude": request.exclude,
                "chapter_target_chars": request.chapter_target_chars,
            },
            creative_brief={
                "synopsis": brief.synopsis,
                "target_readers": brief.target_readers,
                "selling_points": brief.selling_points,
                "protagonist_name": brief.protagonist_name,
                "protagonist_profile": brief.protagonist_profile,
                "character_profiles": [
                    {
                        "name": item.name,
                        "role": item.role,
                        "personality": item.personality,
                        "core_goal": item.core_goal,
                        "relationships": item.relationships,
                    }
                    for item in brief.character_profiles
                ],
                "main_conflict": brief.main_conflict,
                "world_rules": brief.world_rules,
                "style_guide": brief.style_guide,
                "reader_contract": brief.reader_contract,
                "core_expectation": brief.core_expectation,
            },
            chapter_plan={
                "number": plan.number,
                "title": plan.title,
                "objective": plan.objective,
                "conflict": plan.conflict,
                "turning_point": plan.turning_point,
                "hook": plan.hook,
                "scenes": plan.scenes,
                "story_stage": plan.story_stage,
                "plot_unit": plan.plot_unit,
                "chapter_position": plan.chapter_position,
                "target_emotion": plan.target_emotion,
                "reader_payoff": plan.reader_payoff,
                "new_expectation": plan.new_expectation,
                "protagonist_goal": plan.protagonist_goal,
                "critical_choice": plan.critical_choice,
                "opening_hook": plan.opening_hook,
                "forbidden_releases": plan.forbidden_releases,
                "relationship_changes": plan.relationship_changes,
                "information_gap": plan.information_gap,
                "scene_beats": [item.model_dump(mode="json") for item in plan.scene_beats],
            },
            current_draft=content,
            previous_chapter_excerpt=previous_chapter_excerpt[-800:],
        )

    @staticmethod
    def _merge_review_reports(
        base: ReviewReport,
        story_skills: ReviewReport,
    ) -> ReviewReport:
        issues: list[ReviewIssue] = []
        seen: set[tuple[str, str]] = set()
        for issue in [*base.issues, *story_skills.issues]:
            key = (issue.category, issue.message)
            if key in seen:
                continue
            seen.add(key)
            issues.append(issue)
        score = min(base.score, story_skills.score)
        if ReviewDecision.HUMAN_REQUIRED in {
            base.decision,
            story_skills.decision,
        }:
            decision = ReviewDecision.HUMAN_REQUIRED
        elif score > QUALITY_PASS_SCORE:
            decision = ReviewDecision.PASS
        else:
            decision = ReviewDecision.REVISE
        return ReviewReport(
            decision=decision,
            score=score,
            issues=issues,
            checks={**base.checks, **story_skills.checks},
        )

    @staticmethod
    def _merge_character_updates(
        updates: list[CharacterMemoryUpdate],
    ) -> list[CharacterMemoryUpdate]:
        merged: dict[str, CharacterMemoryUpdate] = {}
        for update in updates:
            current = merged.get(update.name)
            if current is None:
                merged[update.name] = update
                continue
            merged[update.name] = current.model_copy(
                update={
                    "role": (
                        "protagonist"
                        if "protagonist" in {current.role, update.role}
                        else update.role or current.role
                    ),
                    "importance": (
                        "core"
                        if "core" in {current.importance, update.importance}
                        else "major"
                    ),
                    "profile": update.profile or current.profile,
                    "immutable_facts": list(
                        dict.fromkeys([*current.immutable_facts, *update.immutable_facts])
                    ),
                    "current_goal": update.current_goal or current.current_goal,
                    "last_result": update.last_result or current.last_result,
                    "location": update.location or current.location,
                    "knowledge_gained": list(
                        dict.fromkeys([*current.knowledge_gained, *update.knowledge_gained])
                    ),
                    "relationship_changes": {
                        **current.relationship_changes,
                        **update.relationship_changes,
                    },
                }
            )
        return list(merged.values())

    @staticmethod
    def _assert_runnable(project: ProjectRecord) -> None:
        if not project.confirmed:
            raise WorkflowBlocked("创作方案尚未确认")
        if project.status == ProjectStatus.PAUSED:
            raise WorkflowBlocked("项目已暂停")
        if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.HUMAN_REQUIRED}:
            raise WorkflowBlocked(f"项目当前状态不可自动写作：{project.status.value}")

    def _review(
        self,
        project: ProjectRecord,
        plan: ChapterPlan,
        protagonist_name: str,
        content: str,
        has_research: bool,
    ) -> ReviewReport:
        issues: list[ReviewIssue] = []
        checks: dict[str, float] = {}
        target = project.request.chapter_target_chars
        length_ratio = len(content) / target
        checks["length"] = min(100.0, round(length_ratio * 100, 1))
        if length_ratio < 0.9:
            issues.append(
                ReviewIssue(
                    category="text",
                    severity=IssueSeverity.MAJOR,
                    message=f"章节长度仅为目标的 {length_ratio:.0%}",
                    suggestion="补足行动细节、因果与人物反应，不得灌水重复",
                )
            )
        forbidden_terms: list[str] = []
        for item in project.request.exclude:
            term = re.sub(r"^(不要|避免|禁止|无)", "", item).strip()
            if term and term in content:
                forbidden_terms.append(term)
                issues.append(
                    ReviewIssue(
                        category="constraint",
                        severity=IssueSeverity.BLOCKER,
                        message=f"正文出现明确排除项：{term}",
                        suggestion=f"删除禁止项：{term}",
                    )
                )
        checks["constraints"] = 100.0 if not forbidden_terms else 0.0
        checks["character"] = 100.0 if protagonist_name in content else 30.0
        if protagonist_name not in content:
            issues.append(
                ReviewIssue(
                    category="character",
                    severity=IssueSeverity.MAJOR,
                    message="主角在正文中缺失",
                    suggestion=f"围绕{protagonist_name}重写关键场景",
                )
            )
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        unique_ratio = len(set(paragraphs)) / max(1, len(paragraphs))
        checks["originality"] = round(unique_ratio * 100, 1)
        if unique_ratio < 0.8:
            issues.append(
                ReviewIssue(
                    category="repetition",
                    severity=IssueSeverity.MAJOR,
                    message="段落重复率过高",
                    suggestion="重写重复段落并增加推进信息",
                )
            )
        checks["plot"] = 100.0 if plan.hook[:8] in content or "下一" in content else 75.0
        checks["factuality"] = 100.0 if not plan.research_questions or has_research else 78.0
        score = round(sum(checks.values()) / len(checks), 1)
        decision = ReviewDecision.PASS if score > QUALITY_PASS_SCORE else ReviewDecision.REVISE
        return ReviewReport(decision=decision, score=score, issues=issues, checks=checks)

    @staticmethod
    def _summary(plan: ChapterPlan, content: str) -> str:
        tail = content.split("\n\n")[-1]
        return f"{plan.objective}。{plan.turning_point}。章末：{tail[:120]}"

    @staticmethod
    def _research_note(
        title: str,
        url: str,
        excerpt: str,
        score: float,
        verified: bool,
    ) -> str:
        status = "可交叉核验" if verified else "单一来源，保守使用"
        return (
            f"来源：{title}；URL：{url or '未提供'}；"
            f"可信度：{score:.2f}；核验：{status}；摘录：{excerpt[:1200]}"
        )

    def _index_pending(
        self,
        run_id: str,
        project_id: str,
        chapter_id: str,
        *,
        progress: int,
    ) -> None:
        try:
            pending = self.repository.list_pending_semantic_inputs(project_id)
            if not pending:
                return
            texts = [str(item["content"])[:8000] for item in pending]
            vectors = self.embedding.embed(texts)
            self.repository.upsert_semantic_documents(
                project_id, pending, vectors, self.embedding.model
            )
            self._emit(
                run_id,
                project_id,
                chapter_id,
                "SemanticIndex",
                f"已向量化 {len(pending)} 条章节摘要或研究资料",
                progress,
                payload={"documents": len(pending), "model": self.embedding.model},
            )
        except Exception as exc:
            self._emit(
                run_id,
                project_id,
                chapter_id,
                "SemanticIndex",
                f"向量索引失败但不阻断写作：{type(exc).__name__}",
                progress,
                level="WARN",
            )

    def _emit(
        self,
        run_id: str,
        project_id: str,
        chapter_id: str | None,
        node: str,
        message: str,
        progress: int,
        *,
        level: str = "INFO",
        payload: dict[str, Any] | None = None,
    ) -> None:
        event = RunEvent(
            run_id=run_id,
            project_id=project_id,
            chapter_id=chapter_id,
            node=node,
            message=message,
            progress=progress,
            level=level,
            payload=payload or {},
        )
        self.repository.add_event(event)
        self._events.append(event)
