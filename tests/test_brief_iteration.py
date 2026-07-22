import tempfile
import unittest
from pathlib import Path

from novel_agent.adapters import DisabledSearchAdapter, OfflineLLMGateway
from novel_agent.models import (
    BriefRegenerateRequest,
    CreativeBrief,
    NovelCreateRequest,
    ProjectStatus,
)
from novel_agent.repository import SQLiteRepository
from novel_agent.service import NovelAgentService


class RevisionGateway(OfflineLLMGateway):
    def revise_brief(self, request, current, feedback, fields):
        data = current.model_dump(mode="json")
        data["selected_title"] = "局部重做后的书名"
        data["title_candidates"] = ["局部重做后的书名", *current.title_candidates]
        data["synopsis"] = "这段简介不应在只重做书名时被采用。"
        return CreativeBrief.model_validate(data)


class BriefIterationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.repository = SQLiteRepository(Path(self.folder.name) / "brief.db")
        self.service = NovelAgentService(
            self.repository, OfflineLLMGateway(), DisabledSearchAdapter()
        )

    def tearDown(self):
        self.folder.cleanup()

    @staticmethod
    def request():
        return NovelCreateRequest(
            preferred_title="用户指定书名",
            genre="末世",
            idea="普通人建立一个资源有限的社区。",
            require_plan_confirmation=True,
            chapter_target_chars=800,
        )

    def test_preferred_title_and_manual_edit_create_versions_and_sync_memory(self):
        project = self.service.create_novel(self.request())
        self.assertEqual(project.title, "用户指定书名")
        self.assertEqual(len(self.repository.list_brief_versions(project.id)), 1)
        data = project.brief.model_dump(mode="json")
        data.update(
            {
                "selected_title": "人工修改后的书名",
                "protagonist": "顾宁",
                "protagonist_name": "顾宁",
                "protagonist_profile": "谨慎的社区维修员",
                "world_rules": ["所有资源都有明确来源"],
            }
        )

        updated = self.service.update_draft_brief(
            project.id, CreativeBrief.model_validate(data)
        )

        self.assertEqual(updated.title, "人工修改后的书名")
        self.assertEqual(len(self.repository.list_brief_versions(project.id)), 2)
        self.assertEqual(self.repository.list_characters(project.id)[0].name, "顾宁")
        self.assertEqual(
            self.repository.list_world_facts(project.id)[0].statement,
            "所有资源都有明确来源",
        )

    def test_partial_regenerate_changes_only_selected_group_and_restore_is_versioned(self):
        self.service.gateway = RevisionGateway()
        project = self.service.create_novel(self.request())
        original_synopsis = project.brief.synopsis

        regenerated = self.service.regenerate_draft_brief(
            project.id, BriefRegenerateRequest(sections=["title"])
        )

        self.assertEqual(regenerated.title, "局部重做后的书名")
        self.assertEqual(regenerated.brief.synopsis, original_synopsis)
        restored = self.service.restore_draft_brief(project.id, 1)
        self.assertEqual(restored.title, "用户指定书名")
        versions = self.repository.list_brief_versions(project.id)
        self.assertEqual([item["version"] for item in versions], [3, 2, 1])
        self.assertEqual(versions[0]["source"], "restore")

    def test_only_unconfirmed_drafts_can_be_deleted_or_edited(self):
        draft = self.service.create_novel(self.request())
        self.service.delete_novel(draft.id)
        self.assertEqual(self.repository.list_projects(), [])

        confirmed = self.service.create_novel(self.request())
        self.service.confirm_novel(confirmed.id)
        self.assertEqual(
            self.repository.get_project(confirmed.id).status, ProjectStatus.ACTIVE
        )
        with self.assertRaises(ValueError):
            self.service.delete_novel(confirmed.id)
        with self.assertRaises(ValueError):
            self.service.update_draft_brief(confirmed.id, confirmed.brief)
        self.service.delete_novel(
            confirmed.id, confirmation_title=confirmed.title
        )
        self.assertEqual(self.repository.list_projects(), [])


if __name__ == "__main__":
    unittest.main()
