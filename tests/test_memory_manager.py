import unittest

from novel_agent.memory_manager import MemoryManager
from novel_agent.models import CharacterRecord, WorldFactRecord


class MemoryManagerTests(unittest.TestCase):
    def test_protagonist_and_locked_rules_survive_tiny_budget(self) -> None:
        manager = MemoryManager(hot_budget_tokens=120)
        protagonist = CharacterRecord(
            id="character-1",
            project_id="novel-1",
            name="顾宁",
            role="protagonist",
            immutable_facts=["主角必须依靠有限资源解决问题"],
            current_state={"last_seen_chapter": 1, "importance": "core"},
        )
        facts = [
            WorldFactRecord(
                id="fact-locked",
                project_id="novel-1",
                category="hard_rule",
                statement="资源不会凭空出现",
                locked=True,
            ),
            WorldFactRecord(
                id="fact-large",
                project_id="novel-1",
                category="recent",
                statement="很长的普通事实" * 100,
                evidence_chapter=9,
            ),
            WorldFactRecord(
                id="fact-short",
                project_id="novel-1",
                category="older",
                statement="旧仓库仍然存在",
                evidence_chapter=1,
            ),
        ]

        context = manager.build_context([protagonist], facts, [], [], 10)

        self.assertTrue(any("角色=顾宁" in item for item in context["compact_characters"]))
        self.assertTrue(any("资源不会凭空出现" in item for item in context["compact_world_facts"]))
        self.assertTrue(any("旧仓库仍然存在" in item for item in context["compact_world_facts"]))
        self.assertFalse(any("很长的普通事实" in item for item in context["compact_world_facts"]))

    def test_only_reliable_or_verified_research_enters_context(self) -> None:
        manager = MemoryManager(hot_budget_tokens=400, research_reliability_min=0.6)
        context = manager.build_context(
            [],
            [],
            [],
            [
                {"title": "低可信来源", "reliability": 0.2, "verified": False},
                {"title": "可靠来源", "reliability": 0.8, "verified": False},
                {"title": "已核验来源", "reliability": 0.3, "verified": True},
            ],
            10,
        )

        joined = "\n".join(context["compact_research"])
        self.assertNotIn("低可信来源", joined)
        self.assertIn("可靠来源", joined)
        self.assertIn("已核验来源", joined)


if __name__ == "__main__":
    unittest.main()
