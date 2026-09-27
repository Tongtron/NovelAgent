import tempfile
import unittest
from pathlib import Path

import novel_agent.service as service_module
from novel_agent.adapters import DisabledSearchAdapter, OfflineLLMGateway
from novel_agent.models import NovelCreateRequest
from novel_agent.repository import SQLiteRepository
from novel_agent.service import NovelAgentService


class RepositoryTests(unittest.TestCase):
    def test_token_daily_summary_counts_distinct_dates(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repository = SQLiteRepository(Path(folder) / "usage.db")
            repository.save_token_usage("2026-07-20", "plan", "model", 10, 20, 30)
            repository.save_token_usage("2026-07-21", "write", "model", 20, 30, 50)
            repository.save_token_usage("2026-07-21", "review", "model", 5, 5, 10)

            summary = repository.get_token_daily_summary("2026-07-20", "2026-07-21")

            self.assertEqual(summary["days"], 2)
            self.assertEqual(summary["total_calls"], 3)
            self.assertEqual(summary["total_tokens"], 90)

    def test_draft_does_not_update_official_memory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repository = SQLiteRepository(Path(folder) / "test.db")
            service = NovelAgentService(repository, OfflineLLMGateway(), DisabledSearchAdapter())
            project = service.create_novel(
                NovelCreateRequest(
                    genre="都市",
                    idea="普通人经营一家社区修理店。",
                    require_plan_confirmation=False,
                    chapter_target_chars=800,
                )
            )
            protagonist_before = repository.list_characters(project.id)[0]
            self.assertNotIn("last_seen_chapter", protagonist_before.current_state)
            result = service.generate_next_chapter(project.id)
            protagonist_after = repository.list_characters(project.id)[0]
            self.assertEqual(protagonist_after.current_state["last_seen_chapter"], result.chapter.number)
            self.assertGreater(protagonist_after.version, protagonist_before.version)

    def test_chapter_versions_keep_draft_history(self) -> None:
        original_validate = service_module.validate_requirements
        service_module.validate_requirements = lambda _request: None
        with tempfile.TemporaryDirectory() as folder:
            repository = SQLiteRepository(Path(folder) / "history.db")
            service = NovelAgentService(repository, OfflineLLMGateway(), DisabledSearchAdapter())
            project = service.create_novel(
                NovelCreateRequest(
                    genre="閮藉競",
                    idea="鏅€氫汉缁忚惀涓€瀹剁ぞ鍖轰慨鐞嗗簵銆?",
                    require_plan_confirmation=False,
                    chapter_target_chars=800,
                )
            )

            service_module.validate_requirements = original_validate
            result = service.generate_next_chapter(project.id)
            versions = repository.list_chapter_versions(project.id, result.chapter.number)

            self.assertGreaterEqual(len(versions), 2)
            self.assertEqual(versions[0]["kind"], "FINAL")
            self.assertEqual(versions[0]["content"], result.chapter.content)
            self.assertTrue(any(version["kind"] == "DRAFT" for version in versions))


if __name__ == "__main__":
    unittest.main()
