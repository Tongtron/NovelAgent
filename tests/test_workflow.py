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
    new_id,
    utc_now,
)
from novel_agent.repository import SQLiteRepository
from novel_agent.service import NovelAgentService
from novel_agent.workflow import WorkflowBlocked


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
