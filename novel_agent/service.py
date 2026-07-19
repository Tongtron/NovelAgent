from __future__ import annotations

from dataclasses import dataclass, field

from novel_agent.adapters import (
    DisabledEmbeddingGateway,
    DisabledSearchAdapter,
    EmbeddingGateway,
    LLMGateway,
    SearchAdapter,
)
from novel_agent.models import (
    BriefRegenerateRequest,
    ChapterStatus,
    CharacterRecord,
    CreativeBrief,
    NovelCreateRequest,
    ProjectRecord,
    ProjectStatus,
    WorkflowResult,
    WorldFactRecord,
    new_id,
    utc_now,
)
from novel_agent.repository import SQLiteRepository
from novel_agent.requirements import validate_requirements
from novel_agent.workflow import ChapterWorkflow


@dataclass(slots=True)
class NovelAgentService:
    repository: SQLiteRepository
    gateway: LLMGateway
    search: SearchAdapter
    max_revision_attempts: int = 2
    embedding: EmbeddingGateway = field(default_factory=DisabledEmbeddingGateway)
    semantic_retrieval_limit: int = 6

    def create_novel(self, request: NovelCreateRequest) -> ProjectRecord:
        validate_requirements(request)
        brief = self.gateway.create_brief(request)
        if request.preferred_title:
            candidates = [request.preferred_title, *brief.title_candidates]
            brief = brief.model_copy(
                update={
                    "selected_title": request.preferred_title,
                    "title_candidates": list(dict.fromkeys(candidates))[:6],
                }
            )
        project_id = new_id("novel")
        now = utc_now()
        confirmed = not request.require_plan_confirmation
        project = ProjectRecord(
            id=project_id,
            title=brief.selected_title,
            status=ProjectStatus.ACTIVE if confirmed else ProjectStatus.DRAFT,
            request=request,
            brief=brief,
            confirmed=confirmed,
            current_chapter=0,
            created_at=now,
            updated_at=now,
        )
        characters = self._character_records(project_id, request, brief)
        facts = [
            WorldFactRecord(
                id=new_id("fact"),
                project_id=project_id,
                category="hard_rule",
                statement=statement,
                locked=True,
            )
            for statement in brief.world_rules
        ]
        return self.repository.create_project(project, characters, facts)

    @staticmethod
    def _character_records(
        project_id: str,
        request: NovelCreateRequest,
        brief: CreativeBrief,
    ) -> list[CharacterRecord]:
        records: list[CharacterRecord] = []
        for profile in brief.character_profiles:
            is_protagonist = profile.name == brief.protagonist_name
            immutable_facts = [
                *([f"人物背景：{profile.profile}"] if profile.profile else []),
                *([f"性格特征：{'、'.join(profile.personality)}"] if profile.personality else []),
            ]
            if is_protagonist:
                immutable_facts = [
                    f"主角姓名为{profile.name}",
                    *immutable_facts,
                    f"故事起点属于{request.genre}",
                    "没有未经故事建立的无限资源或全知能力",
                ]
            records.append(
                CharacterRecord(
                    id=new_id("character"),
                    project_id=project_id,
                    name=profile.name,
                    role="protagonist" if is_protagonist else profile.role,
                    immutable_facts=immutable_facts,
                    current_state={
                        "chapter": 0,
                        "importance": "core" if is_protagonist else profile.importance,
                        "goal": profile.core_goal or (brief.main_conflict if is_protagonist else ""),
                        "knowledge": [],
                        "relationships": profile.relationships,
                    },
                )
            )
        return records

    def confirm_novel(self, project_id: str) -> ProjectRecord:
        return self.repository.confirm_project(project_id)

    def update_draft_brief(
        self, project_id: str, brief: CreativeBrief
    ) -> ProjectRecord:
        self._assert_editable_draft(project_id)
        brief = CreativeBrief.model_validate(brief.model_dump(mode="json"))
        return self.repository.save_draft_brief(
            project_id, brief, source="manual_edit"
        )

    def regenerate_draft_brief(
        self, project_id: str, change: BriefRegenerateRequest
    ) -> ProjectRecord:
        project = self._assert_editable_draft(project_id)
        fields = self._brief_fields(change.sections)
        if change.feedback or change.sections:
            candidate = self.gateway.revise_brief(
                project.request,
                project.brief,
                change.feedback,
                fields,
            )
        else:
            candidate = self.gateway.create_brief(project.request)
        if fields:
            merged = project.brief.model_dump(mode="json")
            generated = candidate.model_dump(mode="json")
            for name in fields:
                merged[name] = generated[name]
            candidate = CreativeBrief.model_validate(merged)
        else:
            candidate = CreativeBrief.model_validate(
                candidate.model_dump(mode="json")
            )
        source = "partial_regenerate" if change.sections else "regenerate"
        if change.feedback:
            source = "feedback_regenerate"
        return self.repository.save_draft_brief(
            project_id,
            candidate,
            source=source,
            feedback=change.feedback,
        )

    def restore_draft_brief(self, project_id: str, version: int) -> ProjectRecord:
        self._assert_editable_draft(project_id)
        brief = self.repository.get_brief_version(project_id, version)
        return self.repository.save_draft_brief(
            project_id, brief, source="restore", feedback=f"恢复 v{version}"
        )

    def delete_draft_novel(self, project_id: str) -> None:
        self._assert_editable_draft(project_id)
        self.repository.delete_draft_project(project_id)

    def pause_novel(self, project_id: str) -> ProjectRecord:
        return self.repository.update_project_status(project_id, ProjectStatus.PAUSED)

    def resume_novel(self, project_id: str) -> ProjectRecord:
        project = self.repository.get_project(project_id)
        if not project.confirmed:
            raise ValueError("创作方案尚未确认")
        return self.repository.update_project_status(project_id, ProjectStatus.ACTIVE)

    def recover_known_issue(self, project_id: str) -> ProjectRecord:
        """Recover only the known legacy protagonist-name false positive."""
        project = self.repository.get_project(project_id)
        if project.status != ProjectStatus.HUMAN_REQUIRED:
            raise ValueError("项目不处于需要人工处理状态")
        diagnostic = self.repository.get_latest_run_diagnostic(project_id)
        if not diagnostic or diagnostic.get("error_code") != "REVIEW_RETRY_EXHAUSTED":
            raise ValueError("当前异常不支持自动恢复，请保留现场进行人工检查")
        report = diagnostic.get("checkpoint", {}).get("report", {})
        issues = report.get("issues", [])
        known = bool(issues) and all(
            issue.get("category") == "character"
            and issue.get("message") == "主角在正文中缺失"
            for issue in issues
        )
        if not known:
            raise ValueError("当前审核包含其他问题，不能自动恢复")
        chapter = self.repository.get_chapter_by_number(
            project_id, project.current_chapter + 1
        )
        if chapter is None or chapter.status != ChapterStatus.FAILED:
            raise ValueError("找不到可安全恢复的失败章节")
        return self.repository.recover_protagonist_name_issue(
            project_id,
            project.brief,
            project.brief.protagonist_name,
            project.brief.protagonist_profile,
            chapter.id,
        )

    def generate_next_chapter(self, project_id: str) -> WorkflowResult:
        workflow = ChapterWorkflow(
            repository=self.repository,
            gateway=self.gateway,
            search=self.search,
            max_revision_attempts=self.max_revision_attempts,
            embedding=self.embedding,
            semantic_retrieval_limit=self.semantic_retrieval_limit,
        )
        return workflow.run(project_id)

    def fill_reserve(self, project_id: str, limit: int | None = None) -> list[WorkflowResult]:
        project = self.repository.get_project(project_id)
        target = project.request.reserve_target
        missing = max(0, target - self.repository.reserve_count(project_id))
        if limit is not None:
            missing = min(missing, max(0, limit))
        results: list[WorkflowResult] = []
        for _ in range(missing):
            results.append(self.generate_next_chapter(project_id))
        return results

    def rebuild_character_archives(self, project_id: str) -> dict[str, object]:
        project = self.repository.get_project(project_id)
        chapters = [
            chapter
            for chapter in self.repository.list_chapters(project_id)
            if chapter.status == ChapterStatus.READY and chapter.content
        ]
        processed = 0
        updated_names: set[str] = set()
        failures: list[dict[str, object]] = []
        for chapter in chapters:
            roster = self.repository.list_characters(project_id)
            try:
                extracted = self.gateway.extract_character_memories(
                    project.brief,
                    chapter.number,
                    chapter.content or "",
                    roster,
                )
                plan_updates = chapter.plan.character_updates if chapter.plan else []
                updates = ChapterWorkflow._merge_character_updates(
                    [*plan_updates, *extracted.characters]
                )
                self.repository.upsert_character_memories(
                    project_id,
                    chapter.number,
                    updates,
                )
                processed += 1
                updated_names.update(item.name for item in updates)
            except Exception as exc:
                failures.append(
                    {"chapter": chapter.number, "error": type(exc).__name__}
                )
        removed_count = 0
        if chapters and not failures:
            keep_names = {
                profile.name for profile in project.brief.character_profiles
            }
            keep_names.update(updated_names)
            removed_count = self.repository.prune_character_archives(
                project_id,
                keep_names,
            )
        return {
            "processed_chapters": processed,
            "character_count": len(self.repository.list_characters(project_id)),
            "updated_characters": sorted(updated_names),
            "removed_nonimportant": removed_count,
            "failures": failures,
        }

    def project_detail(self, project_id: str) -> dict[str, object]:
        project = self.repository.get_project(project_id)
        chapters = self.repository.list_chapters(project_id)
        return {
            "project": project,
            "chapters": chapters,
            "characters": self.repository.list_characters(project_id),
            "world_facts": self.repository.list_world_facts(project_id),
            "timeline": self.repository.list_timeline(project_id),
            "research_sources": self.repository.list_research_sources(project_id),
            "semantic_document_count": self.repository.semantic_document_count(project_id),
            "reserve_count": self.repository.reserve_count(project_id),
        }

    def semantic_search(
        self, project_id: str, query: str, limit: int | None = None
    ) -> list[dict[str, object]]:
        if not self.embedding.enabled:
            raise ValueError("Embedding 未启用，无法执行语义检索")
        query = query.strip()
        if not query:
            raise ValueError("语义检索内容不能为空")
        vector = self.embedding.embed([query])[0]
        return self.repository.semantic_search(
            project_id,
            vector,
            limit=min(max(1, limit or self.semantic_retrieval_limit), 20),
        )

    def _assert_editable_draft(self, project_id: str) -> ProjectRecord:
        project = self.repository.get_project(project_id)
        if project.status != ProjectStatus.DRAFT or project.confirmed:
            raise ValueError("创作方案已确认，不能再按草稿方式修改")
        if self.repository.list_chapters(project_id):
            raise ValueError("项目已经生成章节，不能再按草稿方式修改")
        return project

    @staticmethod
    def _brief_fields(sections: list[str]) -> list[str]:
        groups = {
            "title": ["title_candidates", "selected_title"],
            "story": ["synopsis", "target_readers", "selling_points", "main_conflict"],
            "protagonist": [
                "protagonist",
                "protagonist_name",
                "protagonist_profile",
                "character_profiles",
            ],
            "world": ["world_rules"],
            "opening": ["opening_three_chapters"],
            "style": ["style_guide"],
            "serialization": ["total_chapters", "volume_count", "update_plan"],
        }
        return list(
            dict.fromkeys(field for section in sections for field in groups[section])
        )


def build_service(repository: SQLiteRepository, gateway: LLMGateway) -> NovelAgentService:
    return NovelAgentService(
        repository,
        gateway,
        DisabledSearchAdapter(),
        embedding=DisabledEmbeddingGateway(),
    )
