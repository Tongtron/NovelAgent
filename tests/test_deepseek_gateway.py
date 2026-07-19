import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from novel_agent.adapters import APIConfigurationRequired, build_gateway
from novel_agent.adapters_remote import (
    DeepSeekLLMGateway,
    LLMAuthenticationError,
    LLMResponseError,
)
from novel_agent.config import Settings
from novel_agent.models import ChapterPlan, CreativeBrief, NovelCreateRequest
from novel_agent.repository import SQLiteRepository
from novel_agent.service import NovelAgentService
from novel_agent.adapters import DisabledSearchAdapter


def completion(content: str | None, *, finish_reason: str = "stop") -> dict:
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def brief_payload() -> dict:
    return {
        "title_candidates": ["余烬仓城", "末日库存表", "黎明之前"],
        "selected_title": "余烬仓城",
        "synopsis": "仓储管理员在灾后用有限资源建立社区，并追查灾难源头。",
        "target_readers": "喜欢末世经营与群像成长的读者",
        "selling_points": ["有限资源", "经营建设", "长期伏笔"],
        "protagonist": "林川",
        "main_conflict": "生存效率与共同体规则之间的冲突",
        "world_rules": ["物资不能凭空出现", "人物只能使用已知信息"],
        "total_chapters": 180,
        "volume_count": 6,
        "opening_three_chapters": ["断电", "清点", "第一次冲突"],
        "update_plan": "每天两章，保持五章储备",
        "style_guide": ["移动端短段落", "每章有行动结果"],
    }


def plan_payload(number: int = 1) -> dict:
    return {
        "number": number,
        "title": f"第{number}章 清点",
        "objective": "确认仓库可用物资",
        "conflict": "时间不足且队伍互不信任",
        "turning_point": "封条日期与灾难时间不符",
        "hook": "地下库传来敲击声",
        "scenes": ["进入仓库", "分组清点", "发现异常"],
        "research_questions": [],
    }


class RecordingTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append((url, headers, payload, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class DeepSeekGatewayTests(unittest.TestCase):
    def request(self) -> NovelCreateRequest:
        return NovelCreateRequest(
            genre="末世",
            experiences=["爽文"],
            elements=["囤货", "经营建设"],
            protagonist_tags=["普通人成长"],
            idea="仓储管理员依靠有限资源建立社区。",
            exclude=["无限空间"],
            chapter_target_chars=1200,
        )

    def gateway(self, transport) -> DeepSeekLLMGateway:
        return DeepSeekLLMGateway(
            api_key="test-key",
            transport=transport,
            sleep=lambda _: None,
        )

    def test_create_brief_uses_json_mode_and_pydantic_validation(self) -> None:
        transport = RecordingTransport([completion(json.dumps(brief_payload(), ensure_ascii=False))])
        gateway = self.gateway(transport)
        brief = gateway.create_brief(self.request())
        self.assertIsInstance(brief, CreativeBrief)
        self.assertEqual(brief.selected_title, "余烬仓城")
        url, headers, payload, timeout = transport.calls[0]
        self.assertEqual(url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(payload["model"], "deepseek-v4-pro")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(timeout, 120.0)
        self.assertEqual(gateway.last_usage["total_tokens"], 30)

    def test_plan_chapter_corrects_wrong_model_number(self) -> None:
        wrong = plan_payload(99)
        transport = RecordingTransport([completion(json.dumps(wrong, ensure_ascii=False))])
        gateway = self.gateway(transport)
        plan = gateway.plan_chapter(
            self.request(),
            CreativeBrief.model_validate(brief_payload()),
            3,
            ["上一章完成物资清点"],
            ["地下库异常"],
        )
        self.assertIsInstance(plan, ChapterPlan)
        self.assertEqual(plan.number, 3)

    def test_revise_brief_sends_feedback_and_selected_fields(self) -> None:
        revised_payload = brief_payload()
        revised_payload["selected_title"] = "新方案标题"
        transport = RecordingTransport(
            [completion(json.dumps(revised_payload, ensure_ascii=False))]
        )
        gateway = self.gateway(transport)

        brief = gateway.revise_brief(
            self.request(),
            CreativeBrief.model_validate(brief_payload()),
            "书名更简洁",
            ["title_candidates", "selected_title"],
        )

        self.assertEqual(brief.selected_title, "新方案标题")
        prompt = transport.calls[0][2]["messages"][1]["content"]
        self.assertIn("书名更简洁", prompt)
        self.assertIn("selected_title", prompt)
        self.assertEqual(
            transport.calls[0][2]["response_format"], {"type": "json_object"}
        )

    def test_writer_and_revision_return_plain_text(self) -> None:
        transport = RecordingTransport(
            [completion("第1章 清点\n\n正文内容。"), completion("第1章 清点\n\n修订后的正文。")]
        )
        gateway = self.gateway(transport)
        request = self.request()
        brief = CreativeBrief.model_validate(brief_payload())
        plan = ChapterPlan.model_validate(plan_payload())
        content = gateway.write_chapter(request, brief, plan, "林川", [])
        revised = gateway.revise_chapter(content, ["增加行动细节"], 1200)
        self.assertIn("正文内容", content)
        self.assertIn("修订后的正文", revised)
        self.assertNotIn("response_format", transport.calls[0][2])

    def test_character_extraction_only_keeps_important_people(self) -> None:
        transport = RecordingTransport(
            [completion(json.dumps({"characters": []}, ensure_ascii=False))]
        )
        gateway = self.gateway(transport)

        result = gateway.extract_character_memories(
            CreativeBrief.model_validate(brief_payload()),
            1,
            "快递员小王送完包裹后离开，此后不再出场。",
            [],
        )

        self.assertEqual(result.characters, [])
        prompt = transport.calls[0][2]["messages"][1]["content"]
        self.assertIn("只提取需要长期建档的重要人物", prompt)
        self.assertIn("一次性具名路人", prompt)

    def test_empty_json_response_is_retried_once(self) -> None:
        transport = RecordingTransport(
            [completion(""), completion(json.dumps(brief_payload(), ensure_ascii=False))]
        )
        brief = self.gateway(transport).create_brief(self.request())
        self.assertEqual(brief.selected_title, "余烬仓城")
        self.assertEqual(len(transport.calls), 2)
        retry_prompt = transport.calls[1][2]["messages"][1]["content"]
        self.assertIn("上一次响应为空", retry_prompt)

    def test_invalid_json_is_classified(self) -> None:
        gateway = self.gateway(RecordingTransport([completion("not json")]))
        with self.assertRaises(LLMResponseError):
            gateway.create_brief(self.request())

    def test_authentication_failure_is_not_retried(self) -> None:
        error = urllib.error.HTTPError("url", 401, "unauthorized", None, None)
        transport = RecordingTransport([error])
        gateway = self.gateway(transport)
        with self.assertRaises(LLMAuthenticationError):
            gateway.write_chapter(
                self.request(),
                CreativeBrief.model_validate(brief_payload()),
                ChapterPlan.model_validate(plan_payload()),
                "林川",
                [],
            )
        self.assertEqual(len(transport.calls), 1)

    def test_truncated_output_is_rejected(self) -> None:
        gateway = self.gateway(RecordingTransport([completion("partial", finish_reason="length")]))
        with self.assertRaises(LLMResponseError):
            gateway.write_chapter(
                self.request(),
                CreativeBrief.model_validate(brief_payload()),
                ChapterPlan.model_validate(plan_payload()),
                "林川",
                [],
            )

    def test_build_gateway_requires_key_for_online_deepseek(self) -> None:
        with self.assertRaises(APIConfigurationRequired):
            build_gateway("online", "deepseek", api_key=None)
        gateway = build_gateway("online", "deepseek", api_key="test-key")
        self.assertIsInstance(gateway, DeepSeekLLMGateway)

    def test_settings_supply_deepseek_defaults_and_key_alias(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(
                os.environ,
                {
                    "NOVEL_AGENT_MODE": "online",
                    "LLM_PROVIDER": "deepseek",
                    "DEEPSEEK_API_KEY": "test-key",
                },
                clear=True,
            ):
                settings = Settings.load(Path(folder))
        self.assertEqual(settings.llm_api_key, "test-key")
        self.assertEqual(settings.llm_base_url, "https://api.deepseek.com")
        self.assertEqual(settings.llm_model, "deepseek-v4-pro")
        self.assertTrue(settings.api_readiness()["llm"]["enabled"])

    def test_deepseek_gateway_runs_full_chapter_workflow_with_mock_api(self) -> None:
        paragraphs = [
            f"林川执行第{i}项清点任务，记录物资来源、消耗和结果，并让同伴复核时间顺序。"
            f"这一步带来了新的行动信息，也改变了队伍对仓库规则的理解。"
            for i in range(1, 16)
        ]
        paragraphs.append("新加入的周宁守在地下库门口。敲击声响起，林川决定在下一章确认声音来源。")
        chapter_text = "第1章 清点\n\n" + "\n\n".join(paragraphs)
        character_memory = {
            "characters": [
                {
                    "name": "林川",
                    "role": "protagonist",
                    "current_goal": "确认地下库声音来源",
                    "last_result": "完成仓库物资清点",
                },
                {
                    "name": "周宁",
                    "role": "ally",
                    "profile": "本章新加入队伍的警戒人员",
                    "immutable_facts": ["负责地下库门口警戒"],
                    "current_goal": "协助确认地下库风险",
                    "last_result": "完成门口警戒",
                },
            ]
        }
        transport = RecordingTransport(
            [
                completion(json.dumps(brief_payload(), ensure_ascii=False)),
                completion(json.dumps(plan_payload(), ensure_ascii=False)),
                completion(chapter_text),
                completion(json.dumps(character_memory, ensure_ascii=False)),
            ]
        )
        gateway = self.gateway(transport)
        with tempfile.TemporaryDirectory() as folder:
            repository = SQLiteRepository(Path(folder) / "deepseek.db")
            service = NovelAgentService(repository, gateway, DisabledSearchAdapter())
            request = self.request().model_copy(
                update={"chapter_target_chars": 800, "require_plan_confirmation": False}
            )
            project = service.create_novel(request)
            result = service.generate_next_chapter(project.id)
            characters = repository.list_characters(project.id)
        self.assertEqual(result.chapter.status.value, "READY")
        self.assertEqual(result.review.decision.value, "PASS")
        self.assertEqual(len(transport.calls), 4)
        self.assertIn("周宁", [item.name for item in characters])
        zhou_ning = next(item for item in characters if item.name == "周宁")
        self.assertEqual(zhou_ning.role, "ally")
        self.assertEqual(zhou_ning.current_state["last_seen_chapter"], 1)


if __name__ == "__main__":
    unittest.main()
