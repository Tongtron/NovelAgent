import tempfile
import unittest
from pathlib import Path

from novel_agent.adapters import DisabledSearchAdapter, OfflineLLMGateway
from novel_agent.models import (
    ChapterRecord,
    ChapterStatus,
    CharacterMemoryUpdate,
    CreativeBrief,
    NovelCreateRequest,
    ProjectStatus,
    ReviewDecision,
    ReviewIssue,
    ReviewReport,
    IssueSeverity,
    new_id,
    utc_now,
)
from novel_agent.repository import SQLiteRepository
from novel_agent.service import NovelAgentService
from novel_agent.workflow import WorkflowBlocked


class RecordingQualityGateway(OfflineLLMGateway):
    def __init__(self) -> None:
        self.plan_calls = 0
        self.plan_previous_excerpts: list[str] = []
        self.writer_previous_excerpts: list[str] = []
        self.readability_previous_excerpts: list[str] = []
        self.readability_contexts = []

    def plan_chapter(self, *args, **kwargs):
        self.plan_calls += 1
        self.plan_previous_excerpts.append(kwargs.get("previous_chapter_excerpt", ""))
        return super().plan_chapter(*args, **kwargs)

    def write_chapter(
        self,
        request,
        brief,
        plan,
        protagonist_name,
        research_notes,
        character_roster=None,
        genre_prose_card="",
        compact_characters=None,
        previous_chapter_excerpt="",
    ):
        self.writer_previous_excerpts.append(previous_chapter_excerpt)
        return super().write_chapter(
            request,
            brief,
            plan,
            protagonist_name,
            research_notes,
            character_roster,
            genre_prose_card,
            compact_characters,
            previous_chapter_excerpt,
        )

    def review_readability(self, context):
        self.readability_previous_excerpts.append(context.previous_chapter_excerpt)
        self.readability_contexts.append(context)
        return super().review_readability(context)


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        repository = SQLiteRepository(Path(self.tempdir.name) / "test.db")
        self.service = NovelAgentService(
            repository=repository,
            gateway=OfflineLLMGateway(),
            search=DisabledSearchAdapter(),
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def request(self, confirmation: bool = True) -> NovelCreateRequest:
        return NovelCreateRequest(
            genre="末世",
            experiences=["爽文"],
            elements=["囤货", "经营建设"],
            protagonist_tags=["重生"],
            idea="仓储管理员依靠有限经验建立社区，没有无限资源。",
            exclude=["过度血腥"],
            chapter_target_chars=1000,
            reserve_target=2,
            require_plan_confirmation=confirmation,
        )

    def test_unconfirmed_project_is_blocked(self) -> None:
        project = self.service.create_novel(self.request(True))
        self.assertEqual(project.status, ProjectStatus.DRAFT)
        with self.assertRaises(WorkflowBlocked):
            self.service.generate_next_chapter(project.id)

    def test_each_new_book_starts_with_an_independent_character_archive(self) -> None:
        first = self.service.create_novel(self.request(True))
        second_request = self.request(True).model_copy(
            update={"idea": "气象员在极寒城市组织居民建立供暖互助网络。"}
        )
        second = self.service.create_novel(second_request)

        first_characters = self.service.repository.list_characters(first.id)
        second_characters = self.service.repository.list_characters(second.id)

        self.assertGreaterEqual(len(first_characters), 3)
        self.assertGreaterEqual(len(second_characters), 3)
        self.assertTrue(all(item.project_id == first.id for item in first_characters))
        self.assertTrue(all(item.project_id == second.id for item in second_characters))
        self.assertNotEqual(
            {item.id for item in first_characters},
            {item.id for item in second_characters},
        )
        self.assertEqual(first_characters[0].current_state["importance"], "core")
        self.assertTrue(
            all(
                item.current_state["importance"] == "major"
                for item in first_characters[1:]
            )
        )

    def test_nonimportant_archive_can_be_pruned_without_removing_protagonist(self) -> None:
        project = self.service.create_novel(self.request(True))
        repository = self.service.repository
        repository.upsert_character_memories(
            project.id,
            1,
            [
                CharacterMemoryUpdate(
                    name="临时甲",
                    role="supporting",
                    profile="只在一个场景传递消息的人",
                )
            ],
        )

        keep_names = {profile.name for profile in project.brief.character_profiles}
        removed = repository.prune_character_archives(project.id, keep_names)
        remaining = repository.list_characters(project.id)

        self.assertEqual(removed, 1)
        self.assertNotIn("临时甲", [item.name for item in remaining])
        self.assertEqual(remaining[0].role, "protagonist")

    def test_chapter_closes_loop_and_commits_memory(self) -> None:
        project = self.service.create_novel(self.request(True))
        self.service.confirm_novel(project.id)
        result = self.service.generate_next_chapter(project.id)
        self.assertEqual(result.chapter.status, ChapterStatus.READY)
        self.assertEqual(result.review.decision.value, "PASS")
        self.assertTrue(result.chapter.content)
        detail = self.service.project_detail(project.id)
        self.assertEqual(detail["reserve_count"], 1)
        self.assertEqual(len(detail["timeline"]), 1)
        protagonist = detail["characters"][0]
        self.assertEqual(protagonist.current_state["last_seen_chapter"], 1)

    def test_single_plan_readability_and_previous_chapter_handoff(self) -> None:
        gateway = RecordingQualityGateway()
        with tempfile.TemporaryDirectory() as folder:
            service = NovelAgentService(
                repository=SQLiteRepository(Path(folder) / "quality.db"),
                gateway=gateway,
                search=DisabledSearchAdapter(),
            )
            project = service.create_novel(self.request(False))
            first = service.generate_next_chapter(project.id)
            second = service.generate_next_chapter(project.id)

        self.assertEqual(gateway.plan_calls, 2)
        self.assertEqual(gateway.plan_previous_excerpts[0], "")
        self.assertTrue(gateway.plan_previous_excerpts[1])
        self.assertEqual(gateway.writer_previous_excerpts[0], "")
        self.assertTrue(gateway.writer_previous_excerpts[1])
        self.assertIn(first.chapter.content[-300:], gateway.writer_previous_excerpts[1])
        self.assertEqual(
            gateway.readability_previous_excerpts[1],
            gateway.writer_previous_excerpts[1][-800:],
        )
        readability_context = gateway.readability_contexts[1]
        self.assertNotIn("title_candidates", readability_context.creative_brief)
        self.assertNotIn("daily_chapters", readability_context.creative_request)
        self.assertIn("style_guide", readability_context.creative_brief)
        self.assertLessEqual(len(readability_context.previous_chapter_excerpt), 800)
        nodes = [event.node for event in second.events]
        self.assertIn("Plan", nodes)
        self.assertIn("ReadabilityReview", nodes)

    def test_quality_retry_stays_active_and_reuses_plan(self) -> None:
        class RetryGateway(RecordingQualityGateway):
            def __init__(self) -> None:
                super().__init__()
                self.review_calls = 0

            def review_readability(self, context):
                self.review_calls += 1
                if self.review_calls == 1:
                    return ReviewReport(
                        decision=ReviewDecision.REVISE,
                        score=55,
                        issues=[
                            ReviewIssue(
                                category="opening_hook",
                                severity=IssueSeverity.MAJOR,
                                message="开篇缺少即时冲突",
                                suggestion="用正在发生的具体危险重写开篇",
                            )
                        ],
                        checks={"opening_hook": 70},
                    )
                return super().review_readability(context)

        gateway = RetryGateway()
        with tempfile.TemporaryDirectory() as folder:
            repository = SQLiteRepository(Path(folder) / "retry.db")
            service = NovelAgentService(
                repository=repository,
                gateway=gateway,
                search=DisabledSearchAdapter(),
                max_revision_attempts=0,
            )
            project = service.create_novel(self.request(False))

            failed_results = service.fill_reserve(project.id)
            failed = failed_results[0]
            diagnostic = repository.get_latest_run_diagnostic(project.id)

            self.assertEqual(len(failed_results), 1)
            self.assertEqual(failed.chapter.status, ChapterStatus.FAILED)
            self.assertEqual(gateway.review_calls, 1)
            self.assertEqual(repository.get_project(project.id).status, ProjectStatus.ACTIVE)
            self.assertEqual(diagnostic["error_code"], "REVIEW_RETRY_EXHAUSTED")

            repository.update_project_status(project.id, ProjectStatus.HUMAN_REQUIRED)
            repository.update_run(
                diagnostic["id"],
                "HUMAN_REQUIRED",
                diagnostic["checkpoint"],
                diagnostic["error_code"],
            )
            recovered = service.generate_next_chapter(project.id)

        self.assertEqual(recovered.chapter.status, ChapterStatus.READY)
        self.assertEqual(gateway.review_calls, 2)
        self.assertEqual(gateway.plan_calls, 1)
        self.assertIn("PlanReuse", [event.node for event in recovered.events])

    def test_pause_and_resume_gate_generation(self) -> None:
        project = self.service.create_novel(self.request(False))
        self.service.pause_novel(project.id)
        with self.assertRaises(WorkflowBlocked):
            self.service.generate_next_chapter(project.id)
        resumed = self.service.resume_novel(project.id)
        self.assertEqual(resumed.status, ProjectStatus.ACTIVE)
        self.assertEqual(self.service.generate_next_chapter(project.id).chapter.number, 1)

    def test_fill_reserve_generates_missing_chapters_only(self) -> None:
        project = self.service.create_novel(self.request(False))
        results = self.service.fill_reserve(project.id)
        self.assertEqual(len(results), 2)
        self.assertEqual(self.service.repository.reserve_count(project.id), 2)
        self.assertEqual(self.service.fill_reserve(project.id), [])

    def test_rework_rolls_back_all_later_derived_data(self) -> None:
        project = self.service.create_novel(self.request(False))
        first = self.service.generate_next_chapter(project.id).chapter
        second = self.service.generate_next_chapter(project.id).chapter
        repository = self.service.repository
        repository.upsert_character_memories(
            project.id,
            2,
            [
                CharacterMemoryUpdate(
                    name=project.brief.protagonist_name,
                    role="protagonist",
                    importance="core",
                    immutable_facts=["第二章才发现的秘密"],
                )
            ],
        )
        sources = repository.save_research_sources(
            project.id,
            second.id,
            [{"title": "第二章资料", "content": "只属于第二章", "reliability": 0.9}],
        )
        repository.upsert_semantic_documents(
            project.id,
            [
                {
                    "source_type": "chapter_summary",
                    "source_id": second.id,
                    "chapter_id": second.id,
                    "content": second.summary or "第二章摘要",
                },
                {
                    "source_type": "research_source",
                    "source_id": str(sources[0]["id"]),
                    "chapter_id": second.id,
                    "content": "只属于第二章",
                },
            ],
            [[1.0, 0.0], [0.0, 1.0]],
            "test-embedding",
        )
        with repository.transaction() as conn:
            conn.execute(
                """INSERT INTO foreshadows
                (project_id,label,status,introduced_chapter,resolved_chapter)
                VALUES (?,?,?,?,?)""",
                (project.id, "第二章伏笔", "OPEN", 2, None),
            )

        rewound = self.service.rework_chapter(
            project.id, 2, feedback="减少生硬对话"
        )

        self.assertEqual(rewound.current_chapter, 1)
        reset_second = repository.get_chapter(second.id)
        self.assertEqual(reset_second.status, ChapterStatus.PLANNED)
        self.assertIsNone(reset_second.content)
        self.assertIsNone(reset_second.plan)
        self.assertEqual(reset_second.version, 0)
        self.assertEqual([item["chapter_number"] for item in repository.list_timeline(project.id)], [1])
        self.assertEqual(repository.list_research_sources(project.id), [])
        self.assertEqual(repository.semantic_document_count(project.id), 0)
        self.assertEqual(repository.list_foreshadows(project.id), [])
        protagonist = repository.list_characters(project.id)[0]
        self.assertNotIn("第二章才发现的秘密", protagonist.immutable_facts)
        self.assertEqual(protagonist.current_state["last_seen_chapter"], 1)
        self.assertEqual(
            repository.get_setting(f"rework_feedback:{second.id}"),
            "减少生硬对话",
        )

        regenerated = self.service.generate_next_chapter(project.id).chapter
        self.assertEqual(regenerated.number, 2)
        self.assertEqual(regenerated.status, ChapterStatus.READY)
        self.assertIsNone(repository.get_setting(f"rework_feedback:{second.id}"))
        self.assertEqual(repository.get_chapter(first.id).status, ChapterStatus.READY)

    def test_legacy_protagonist_biography_is_normalized_to_name(self) -> None:
        brief = self.service.gateway.create_brief(self.request(False)).model_dump(mode="json")
        biography = "方远，前世在末世中挣扎十年，重生后冷静果断"
        brief["protagonist"] = biography
        brief.pop("protagonist_name", None)
        brief.pop("protagonist_profile", None)

        normalized = CreativeBrief.model_validate(brief)

        self.assertEqual(normalized.protagonist, "方远")
        self.assertEqual(normalized.protagonist_name, "方远")
        self.assertEqual(normalized.protagonist_profile, biography)

    def test_recover_known_legacy_character_name_false_positive(self) -> None:
        repository = self.service.repository
        project = self.service.create_novel(self.request(False))
        biography = "方远，前世在末世中挣扎十年，重生后冷静果断"
        brief = project.brief.model_dump(mode="json")
        brief["protagonist"] = biography
        brief.pop("protagonist_name", None)
        brief.pop("protagonist_profile", None)
        chapter = repository.create_chapter(
            ChapterRecord(
                id=new_id("chapter"),
                project_id=project.id,
                number=1,
                title="末日倒计时",
                status=ChapterStatus.PLANNED,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        repository.save_draft_version(chapter.id, "方远检查了仓库。", {"test": True})
        repository.update_chapter(chapter.id, status=ChapterStatus.FAILED)
        run_id = new_id("run")
        repository.create_run(run_id, project.id, chapter.id)
        repository.update_run(
            run_id,
            "HUMAN_REQUIRED",
            {
                "node": "ReviewDecision",
                "report": {
                    "issues": [
                        {
                            "category": "character",
                            "severity": "MAJOR",
                            "message": "主角在正文中缺失",
                            "suggestion": f"围绕{biography}重写关键场景",
                        }
                    ]
                },
            },
            "REVIEW_RETRY_EXHAUSTED",
        )
        with repository.transaction() as conn:
            conn.execute(
                "UPDATE projects SET brief_json=?,status=? WHERE id=?",
                (repository._dump(brief), ProjectStatus.HUMAN_REQUIRED.value, project.id),
            )
            conn.execute(
                """UPDATE characters SET name=?,immutable_facts_json=?
                WHERE project_id=? AND role='protagonist'""",
                (biography, repository._dump([f"主角姓名为{biography}"]), project.id),
            )

        recovered = self.service.recover_known_issue(project.id)

        self.assertEqual(recovered.status, ProjectStatus.ACTIVE)
        self.assertEqual(recovered.brief.protagonist_name, "方远")
        self.assertEqual(repository.list_characters(project.id)[0].name, "方远")
        restored_chapter = repository.get_chapter(chapter.id)
        self.assertEqual(restored_chapter.status, ChapterStatus.PLANNED)
        self.assertEqual(restored_chapter.version, 1)


if __name__ == "__main__":
    unittest.main()
