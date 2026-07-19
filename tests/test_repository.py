import tempfile
import unittest
from pathlib import Path

from novel_agent.adapters import DisabledSearchAdapter, OfflineLLMGateway
from novel_agent.models import NovelCreateRequest
from novel_agent.repository import SQLiteRepository
from novel_agent.service import NovelAgentService


class RepositoryTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

