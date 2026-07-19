import tempfile
import unittest
from pathlib import Path

from novel_agent.adapters import OfflineLLMGateway
from novel_agent.adapters_remote import (
    OpenAICompatibleEmbeddingGateway,
    TavilySearchAdapter,
)
from novel_agent.models import NovelCreateRequest
from novel_agent.repository import SQLiteRepository
from novel_agent.service import NovelAgentService


class QueueTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append((url, headers, payload, timeout))
        return self.responses.pop(0)


class FakeSearch:
    name = "fake-search"
    enabled = True

    def research(self, questions):
        return [
            {
                "query": questions[0],
                "title": "应急用电安全指南",
                "url": "https://example.gov.cn/power-safety",
                "domain": "example.gov.cn",
                "reliability": 0.94,
                "excerpt": "备用电源需要隔离、通风，并由具备资质的人员操作。",
                "content": "备用电源需要隔离、通风，并由具备资质的人员操作。",
                "verified": True,
                "verification_notes": "测试交叉核验",
            }
        ]


class FakeEmbedding:
    name = "fake-embedding"
    model = "test-embedding-v1"
    enabled = True

    def embed(self, texts):
        return [
            [
                float(len(text) % 17 + 1),
                float(text.count("电力") + text.count("电源") + 1),
                float(text.count("章节") + 1),
            ]
            for text in texts
        ]


class ResearchAndEmbeddingTests(unittest.TestCase):
    def test_tavily_search_reads_content_and_marks_cross_source(self):
        transport = QueueTransport(
            [
                {
                    "results": [
                        {
                            "title": "官方指南",
                            "url": "https://safety.example.gov.cn/guide",
                            "score": 0.9,
                            "raw_content": "应急电源应保持通风。",
                        },
                        {
                            "title": "大学实验室说明",
                            "url": "https://lab.example.edu/power",
                            "score": 0.8,
                            "raw_content": "发电设备需要避免一氧化碳聚集。",
                        },
                    ]
                }
            ]
        )
        adapter = TavilySearchAdapter(api_key="test", transport=transport)

        results = adapter.research(["备用发电设备有哪些安全边界？"])

        self.assertEqual(len(results), 2)
        self.assertTrue(all(item["verified"] for item in results))
        self.assertGreater(results[0]["reliability"], 0.8)
        self.assertIn("通风", results[0]["content"])
        self.assertTrue(transport.calls[0][0].endswith("/search"))
        self.assertNotIn("test", str(transport.calls[0][2]))

    def test_openai_compatible_embedding_orders_and_validates_vectors(self):
        transport = QueueTransport(
            [
                {
                    "data": [
                        {"index": 1, "embedding": [0.0, 1.0]},
                        {"index": 0, "embedding": [1.0, 0.0]},
                    ]
                }
            ]
        )
        gateway = OpenAICompatibleEmbeddingGateway(
            provider="siliconflow",
            api_key="test",
            base_url="https://api.siliconflow.cn/v1",
            transport=transport,
        )

        vectors = gateway.embed(["第一章", "研究资料"])

        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])
        self.assertTrue(transport.calls[0][0].endswith("/v1/embeddings"))

    def test_workflow_persists_research_and_indexes_summary_and_source(self):
        with tempfile.TemporaryDirectory() as folder:
            repository = SQLiteRepository(Path(folder) / "semantic.db")
            service = NovelAgentService(
                repository,
                OfflineLLMGateway(),
                FakeSearch(),
                embedding=FakeEmbedding(),
            )
            project = service.create_novel(
                NovelCreateRequest(
                    genre="末世",
                    elements=["电力"],
                    idea="社区需要安全恢复有限电力供应。",
                    chapter_target_chars=800,
                    require_plan_confirmation=False,
                )
            )

            result = service.generate_next_chapter(project.id)
            detail = service.project_detail(project.id)
            matches = service.semantic_search(project.id, "电力安全和备用电源", 5)

        self.assertEqual(result.chapter.status.value, "READY")
        self.assertEqual(len(detail["research_sources"]), 1)
        self.assertEqual(detail["semantic_document_count"], 2)
        self.assertEqual({item["source_type"] for item in matches}, {
            "chapter_summary",
            "research_source",
        })


if __name__ == "__main__":
    unittest.main()
