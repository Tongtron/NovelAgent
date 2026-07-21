import tempfile
import unittest
from pathlib import Path

from novel_agent.adapters import DisabledSearchAdapter, OfflineLLMGateway
from novel_agent.models import ChapterPlan, ChapterStatus, NovelCreateRequest
from novel_agent.repository import SQLiteRepository
from novel_agent.service import NovelAgentService
from novel_agent.writing_skills import OH_STORY_COMMIT, OhStoryWritingRuntime


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RecordingWritingGateway(OfflineLLMGateway):
    def __init__(self) -> None:
        self.plan_guidance: dict[str, str] = {}
        self.writer_card = ""
        self.deslop_calls = 0
        self.deslop_instructions = ""

    def plan_chapter(
        self,
        request,
        brief,
        chapter_number,
        recent_summaries,
        open_threads,
        character_roster=None,
        writing_guidance=None,
    ):
        self.plan_guidance = writing_guidance or {}
        return super().plan_chapter(
            request,
            brief,
            chapter_number,
            recent_summaries,
            open_threads,
            character_roster,
            writing_guidance,
        )

    def write_chapter(
        self,
        request,
        brief,
        plan,
        protagonist_name,
        research_notes,
        character_roster=None,
        genre_prose_card="",
    ):
        self.writer_card = genre_prose_card
        return super().write_chapter(
            request,
            brief,
            plan,
            protagonist_name,
            research_notes,
            character_roster,
            genre_prose_card,
        )

    def deslop_chapter(
        self,
        content,
        plan,
        genre_prose_card,
        skill_instructions,
    ):
        self.deslop_calls += 1
        self.deslop_instructions = skill_instructions
        return super().deslop_chapter(
            content,
            plan,
            genre_prose_card,
            skill_instructions,
        )


class OhStoryWritingRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime = OhStoryWritingRuntime.load(PROJECT_ROOT)

    def test_loads_pinned_skills_and_matches_one_genre_card(self) -> None:
        readiness = self.runtime.readiness()
        self.assertTrue(readiness["enabled"])
        self.assertEqual(readiness["version"], OH_STORY_COMMIT[:12])
        self.assertEqual(readiness["skills"], ["story-deslop", "story-long-write"])
        self.assertGreaterEqual(readiness["genre_cards"], 30)

        card = self.runtime.genre_card("末世", "男频")
        self.assertIsNotNone(card)
        self.assertEqual(card.name, "科幻末世")
        self.assertIn("资源不足", card.prompt_excerpt())

    def test_normalize_plan_supplies_emotion_and_detailed_beats(self) -> None:
        legacy = ChapterPlan(
            number=20,
            title="第20章 旧计划",
            objective="拿到通行证",
            conflict="守门人拒绝放行",
            turning_point="主角发现旧名单",
            hook="名单上出现失踪者",
            scenes=["提出申请", "遭到拒绝", "找到名单"],
        )

        plan = self.runtime.normalize_plan(
            legacy,
            total_chapters=100,
            target_chars=1800,
        )

        self.assertEqual(plan.story_stage, "发展期")
        self.assertTrue(plan.target_emotion)
        self.assertEqual(plan.protagonist_goal, "拿到通行证")
        self.assertEqual(len(plan.scene_beats), 3)
        self.assertEqual(sum(item.target_chars for item in plan.scene_beats), 1800)
        self.assertTrue(all(item.emotional_shift for item in plan.scene_beats))

    def test_prose_audit_blocks_deterministic_ai_patterns(self) -> None:
        content = (
            "第1章 门外\n\n"
            "他不是害怕，而是绝望。她声音不大，却带着不容置疑的力量。\n\n"
            "他不知道的是，更大的风暴才刚刚开始。"
        )

        audit = self.runtime.audit_prose(content)
        codes = {item.code for item in audit.findings}

        self.assertIn("not-is-comparison", codes)
        self.assertIn("voice-contrast", codes)
        self.assertIn("trailer-ending", codes)
        self.assertEqual(self.runtime.as_review_report(audit).decision.value, "REVISE")

    def test_workflow_invokes_genre_card_and_deslop(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            gateway = RecordingWritingGateway()
            service = NovelAgentService(
                repository=SQLiteRepository(Path(folder) / "test.db"),
                gateway=gateway,
                search=DisabledSearchAdapter(),
                writing_skills=self.runtime,
            )
            project = service.create_novel(
                NovelCreateRequest(
                    genre="末世",
                    experiences=["紧张", "爽感"],
                    elements=["囤货", "经营建设"],
                    idea="仓储管理员在灾变后建立互助社区。",
                    chapter_target_chars=1000,
                    require_plan_confirmation=False,
                )
            )

            result = service.generate_next_chapter(project.id)

        self.assertEqual(result.chapter.status, ChapterStatus.READY)
        self.assertEqual(gateway.plan_guidance["genre_card_name"], "科幻末世")
        self.assertIn("资源不足", gateway.writer_card)
        self.assertEqual(gateway.deslop_calls, 1)
        self.assertIn("story-deslop", gateway.deslop_instructions)
        self.assertTrue(result.chapter.plan.target_emotion)
        self.assertTrue(result.chapter.plan.scene_beats)
        nodes = [event.node for event in result.events]
        self.assertIn("GenreProseCard", nodes)
        self.assertIn("ProsePolish", nodes)


if __name__ == "__main__":
    unittest.main()
