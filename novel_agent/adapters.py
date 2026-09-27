from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Protocol

from novel_agent.models import (
    ChapterPlan,
    ChapterPlanBatch,
    ChapterPlanSelection,
    CharacterMemoryBatch,
    CharacterMemoryUpdate,
    CharacterProfile,
    CharacterRecord,
    ContinuityContext,
    CreativeBrief,
    NovelCreateRequest,
    PlanCandidateEvaluation,
    ReadabilityContext,
    ReviewDecision,
    ReviewReport,
)


class APIConfigurationRequired(RuntimeError):
    pass


class LLMGateway(Protocol):
    name: str

    def create_brief(self, request: NovelCreateRequest) -> CreativeBrief: ...

    def revise_brief(
        self,
        request: NovelCreateRequest,
        current: CreativeBrief,
        feedback: str,
        fields: list[str],
    ) -> CreativeBrief: ...

    def plan_chapter(
        self,
        request: NovelCreateRequest,
        brief: CreativeBrief,
        chapter_number: int,
        recent_summaries: list[str],
        open_threads: list[str],
        character_roster: list[CharacterRecord] | None = None,
        writing_guidance: dict[str, str] | None = None,
        compact_characters: list[str] | None = None,
        compact_world_facts: list[str] | None = None,
        compact_timeline: list[str] | None = None,
        previous_chapter_excerpt: str = "",
    ) -> ChapterPlan: ...

    def plan_chapter_candidates(
        self,
        request: NovelCreateRequest,
        brief: CreativeBrief,
        chapter_number: int,
        recent_summaries: list[str],
        open_threads: list[str],
        character_roster: list[CharacterRecord] | None = None,
        writing_guidance: dict[str, str] | None = None,
        compact_characters: list[str] | None = None,
        compact_world_facts: list[str] | None = None,
        compact_timeline: list[str] | None = None,
        previous_chapter_excerpt: str = "",
    ) -> ChapterPlanBatch: ...

    def select_chapter_plan(
        self,
        candidates: list[ChapterPlan],
        previous_chapter_excerpt: str = "",
    ) -> ChapterPlanSelection: ...

    def write_chapter(
        self,
        request: NovelCreateRequest,
        brief: CreativeBrief,
        plan: ChapterPlan,
        protagonist_name: str,
        research_notes: list[str],
        character_roster: list[CharacterRecord] | None = None,
        genre_prose_card: str = "",
        compact_characters: list[str] | None = None,
        previous_chapter_excerpt: str = "",
    ) -> str: ...

    def deslop_chapter(
        self,
        content: str,
        plan: ChapterPlan,
        genre_prose_card: str,
        skill_instructions: str,
    ) -> str: ...

    def revise_chapter(self, content: str, instructions: list[str], target_chars: int) -> str: ...

    def extract_character_memories(
        self,
        brief: CreativeBrief,
        chapter_number: int,
        content: str,
        character_roster: list[CharacterRecord] | None = None,
    ) -> CharacterMemoryBatch: ...

    def review_continuity(
        self,
        context: ContinuityContext,
        skill_instructions: str,
    ) -> ReviewReport: ...

    def review_readability(self, context: ReadabilityContext) -> ReviewReport: ...


class SearchAdapter(Protocol):
    name: str
    enabled: bool

    def research(self, questions: list[str]) -> list[dict[str, object]]: ...


class EmbeddingGateway(Protocol):
    name: str
    model: str
    enabled: bool

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class PublisherAdapter(Protocol):
    name: str

    def publish(self, project_id: str, chapter_id: str, title: str, content: str) -> dict[str, str]: ...


@dataclass(slots=True)
class DisabledSearchAdapter:
    name: str = "disabled"
    enabled: bool = False

    def research(self, questions: list[str]) -> list[dict[str, object]]:
        return []


@dataclass(slots=True)
class DisabledEmbeddingGateway:
    name: str = "disabled"
    model: str = "disabled"
    enabled: bool = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        return []


@dataclass(slots=True)
class MockPublisher:
    name: str = "mock"

    def publish(self, project_id: str, chapter_id: str, title: str, content: str) -> dict[str, str]:
        fingerprint = hashlib.sha256(f"{project_id}:{chapter_id}".encode()).hexdigest()[:16]
        return {"status": "MOCK_PUBLISHED", "external_id": f"mock_{fingerprint}"}


@dataclass(slots=True)
class DisabledRemoteGateway:
    """Deliberate safety gate: it never sends content to a third party."""

    provider: str
    name: str = "remote-disabled-until-implemented"

    def _blocked(self) -> None:
        raise APIConfigurationRequired(
            f"已选择远程模型 {self.provider!r}，但远程适配器仍处于受控状态。"
            "请先完成 novel_agent/adapters_remote.py 中的供应商实现与密钥配置。"
        )

    def create_brief(self, request: NovelCreateRequest) -> CreativeBrief:
        self._blocked()

    def revise_brief(self, *args: object, **kwargs: object) -> CreativeBrief:
        self._blocked()

    def plan_chapter(self, *args: object, **kwargs: object) -> ChapterPlan:
        self._blocked()

    def plan_chapter_candidates(self, *args: object, **kwargs: object) -> ChapterPlanBatch:
        self._blocked()

    def select_chapter_plan(self, *args: object, **kwargs: object) -> ChapterPlanSelection:
        self._blocked()

    def write_chapter(self, *args: object, **kwargs: object) -> str:
        self._blocked()

    def revise_chapter(self, *args: object, **kwargs: object) -> str:
        self._blocked()

    def deslop_chapter(self, *args: object, **kwargs: object) -> str:
        self._blocked()

    def extract_character_memories(self, *args: object, **kwargs: object) -> CharacterMemoryBatch:
        self._blocked()

    def review_continuity(self, *args: object, **kwargs: object) -> ReviewReport:
        self._blocked()

    def review_readability(self, *args: object, **kwargs: object) -> ReviewReport:
        self._blocked()


class OfflineLLMGateway:
    """Deterministic author simulator used to exercise the complete system offline."""

    name = "offline-deterministic"

    _genre_nouns = {
        "末世": ("余烬", "仓城", "黎明"),
        "玄幻": ("天命", "山河", "星火"),
        "都市": ("霓虹", "逆流", "新局"),
        "悬疑": ("回声", "暗门", "证词"),
        "科幻": ("群星", "边界", "归航"),
        "历史": ("长风", "孤城", "山河"),
    }

    def create_brief(self, request: NovelCreateRequest) -> CreativeBrief:
        nouns = self._genre_nouns.get(request.genre, ("长夜", "新生", "征途"))
        key_element = request.elements[0] if request.elements else "成长"
        title_candidates = [
            f"{nouns[0]}之后：从{key_element}开始",
            f"{nouns[1]}生存手册",
            f"向着{nouns[2]}经营世界",
        ]
        protagonist = self._protagonist_name(request)
        digest = int(hashlib.md5(request.idea.encode()).hexdigest()[:8], 16)
        surnames = ["程", "顾", "沈", "许", "陆", "江", "苏", "叶"]
        given = ["岚", "衡", "宁", "序", "野", "舟", "澈", "遥"]
        ally_name = surnames[(digest // 11) % len(surnames)] + given[(digest // 17) % len(given)]
        rival_name = surnames[(digest // 23) % len(surnames)] + given[(digest // 29) % len(given)]
        if ally_name == protagonist:
            ally_name = "程岚"
        if rival_name in {protagonist, ally_name}:
            rival_name = "顾衡"
        experiences = "、".join(request.experiences) or "紧张而有阶段回报"
        elements = "、".join(request.elements) or "生存、成长"
        synopsis = (
            f"{protagonist}原本只是一个被生活推着前进的普通人。"
            f"当{request.genre}秩序骤然改变，关于“{request.idea}”的念头成为唯一可执行的起点。"
            f"主角必须凭有限信息与真实代价，把{elements}变成一套能让同伴活下去的秩序，"
            "同时查清变化背后的因果，并在每一次阶段胜利后面对更难的选择。"
        )
        must = request.must_have or [request.genre, key_element]
        rules = [
            f"{request.genre}的核心规则一经确立不得随意改变",
            "资源、能力与成果必须存在可追踪的来源和代价",
            "人物只能使用其已知或已亲自确认的信息",
            f"必须持续体现：{'、'.join(must)}",
        ]
        style = [
            (
                "男频频道侧重清晰目标、行动升级、阶段反馈与主线推进"
                if request.audience_channel == "男频"
                else "女频频道侧重人物主体性、情感因果、关系变化与成长回报"
            ),
            f"整体体验：{experiences}",
            "每章必须有明确目标、阻力、转折和章末推进钩子",
            "阶段回报建立在因果与行动上，不依赖对手突然降智",
            "短段落、强动作、少空泛解释，避免重复上一章信息",
        ]
        return CreativeBrief(
            title_candidates=title_candidates,
            selected_title=title_candidates[0],
            synopsis=synopsis,
            target_readers=(
                f"偏好{request.audience_channel}、{request.genre}、{experiences}"
                "与长期成长线的连载读者"
            ),
            selling_points=[
                f"{request.genre}+{key_element}的清晰题材组合",
                "有限资源与可追踪建设带来的持续成就感",
                "人物状态、世界规则和伏笔由结构化记忆长期维护",
            ],
            protagonist=protagonist,
            character_profiles=[
                CharacterProfile(
                    name=protagonist,
                    role="protagonist",
                    profile="在秩序变化中承担核心选择的人物，行动谨慎且重视事实依据。",
                    personality=["冷静", "有责任感"],
                    core_goal=f"在{request.genre}环境中建立可持续的生存秩序",
                ),
                CharacterProfile(
                    name=ally_name,
                    role="ally",
                    profile="主角早期结识的行动伙伴，擅长把争论转化为可验证任务。",
                    personality=["务实", "独立判断"],
                    core_goal="保护同伴，同时确认主角的方案是否值得信任",
                    relationships={protagonist: "谨慎合作"},
                ),
                CharacterProfile(
                    name=rival_name,
                    role="rival",
                    profile="与主角争夺关键资源和话语权的竞争者，有自己的现实利益。",
                    personality=["强势", "善于试探"],
                    core_goal="扩大自身控制力并获得稀缺资源",
                    relationships={protagonist: "利益冲突"},
                ),
            ],
            main_conflict=f"在{request.genre}环境中，把个人生存优势转化为可持续共同体秩序",
            world_rules=rules,
            total_chapters=max(120, request.reserve_target * 20),
            volume_count=6,
            opening_three_chapters=[
                "异常征兆出现，主角用职业经验抢到第一项关键先机",
                "旧秩序失效，主角为一个具体选择付出代价并建立最初同盟",
                "临时安全点遭遇第一次系统性危机，主线谜团露出证据",
            ],
            update_plan=(
                f"每天目标 {request.daily_chapters} 章；单章约 {request.chapter_target_chars} 字；"
                f"READY 储备安全线 {request.reserve_target} 章"
            ),
            style_guide=style,
            reader_contract=f"持续交付{experiences}，让主角通过可验证的选择推进{request.genre}主线",
            core_expectation="主角用有限资源取得阶段成果，并发现更高层级的阻力",
        )

    def revise_brief(
        self,
        request: NovelCreateRequest,
        current: CreativeBrief,
        feedback: str,
        fields: list[str],
    ) -> CreativeBrief:
        fresh = self.create_brief(request)
        updates = fresh.model_dump(mode="json")
        if feedback:
            updates["synopsis"] = f"{fresh.synopsis} 方案调整要求：{feedback[:200]}。"
        return CreativeBrief.model_validate(updates)

    def plan_chapter(
        self,
        request: NovelCreateRequest,
        brief: CreativeBrief,
        chapter_number: int,
        recent_summaries: list[str],
        open_threads: list[str],
        character_roster: list[CharacterRecord] | None = None,
        writing_guidance: dict[str, str] | None = None,
        compact_characters: list[str] | None = None,
        compact_world_facts: list[str] | None = None,
        compact_timeline: list[str] | None = None,
        previous_chapter_excerpt: str = "",
    ) -> ChapterPlan:
        arc = (chapter_number - 1) // 10 + 1
        step = (chapter_number - 1) % 10 + 1
        element = request.elements[(chapter_number - 1) % len(request.elements)] if request.elements else "生存"
        prior = recent_summaries[-1] if recent_summaries else "异常刚刚显现"
        objective = f"推进第{arc}阶段目标，并把“{element}”转化为一次可验证的成果"
        if chapter_number <= 3:
            objective = brief.opening_three_chapters[chapter_number - 1]
        thread = open_threads[0] if open_threads else "异常来源"
        title = f"第{chapter_number}章 {element}的第{step}步"
        research_questions = []
        risky_terms = ("医疗", "药", "枪", "法律", "化学", "电力", "农业", "天气", "地图")
        if any(term in request.idea + "".join(request.elements) for term in risky_terms):
            research_questions.append(f"本章涉及{element}时有哪些常识边界与高风险细节？")
        present = [
            item for item in (character_roster or []) if item.role != "protagonist"
        ]
        supporting = present[(chapter_number - 1) % len(present)] if present else None
        character_updates = [
            CharacterMemoryUpdate(
                name=brief.protagonist_name,
                role="protagonist",
                importance="core",
                current_goal=objective,
                last_result=f"围绕{element}完成本章行动并发现新线索",
            )
        ]
        if supporting:
            character_updates.append(
                CharacterMemoryUpdate(
                    name=supporting.name,
                    role=supporting.role,
                    current_goal=f"协助验证{element}方案并维护自身立场",
                    last_result="参与本章行动，关系与立场受到新事件影响",
                    relationship_changes={brief.protagonist_name: "共同经历本章危机"},
                )
            )
        return ChapterPlan(
            number=chapter_number,
            title=title,
            objective=objective,
            conflict=f"有限资源、时间压力与他人对{element}方案的不信任同时出现",
            turning_point=f"从上一阶段信息中发现“{prior[:28]}”并非偶然，线索指向{thread}",
            hook=f"一条本不该出现的记录，把下一步行动指向{thread}",
            scenes=[
                "承接上章结果，明确本章可衡量的行动目标",
                f"围绕{element}展开侦察、分工与风险判断",
                "计划遭遇现实阻力，人物立场在压力中分化",
                "主角用已有知识完成有限反制并承担代价",
                "阶段成果落地，同时抛出更大的因果问题",
            ],
            research_questions=research_questions,
            character_updates=character_updates,
            story_stage="开篇期" if chapter_number <= max(3, brief.total_chapters * 0.15) else "发展期",
            plot_unit=f"剧情单元-{arc}",
            chapter_position="推进",
            target_emotion="不确定与紧张 → 取得阶段成果后的掌控感",
            reader_payoff=f"看见{element}方案产生可验证成果",
            new_expectation=f"{thread}背后的原因将如何改变下一步行动",
            protagonist_goal=objective,
            critical_choice="在保留撤退路线的前提下承担有限代价，验证关键方案",
            opening_hook="上一阶段留下的异常记录出现新的现实后果",
            forbidden_releases=["异常来源的最终真相", "无代价解决全部资源问题"],
            plotline_progress={"主线": objective, "谜团线": f"获得关于{thread}的新证据"},
            relationship_changes=(
                {supporting.name: "从谨慎合作转为愿意共同承担一次风险"}
                if supporting else {}
            ),
            information_gap=[f"主角知道记录异常，其他人只看见{element}行动的结果"],
        )

    def plan_chapter_candidates(
        self,
        request: NovelCreateRequest,
        brief: CreativeBrief,
        chapter_number: int,
        recent_summaries: list[str],
        open_threads: list[str],
        character_roster: list[CharacterRecord] | None = None,
        writing_guidance: dict[str, str] | None = None,
        compact_characters: list[str] | None = None,
        compact_world_facts: list[str] | None = None,
        compact_timeline: list[str] | None = None,
        previous_chapter_excerpt: str = "",
    ) -> ChapterPlanBatch:
        base = self.plan_chapter(
            request,
            brief,
            chapter_number,
            recent_summaries,
            open_threads,
            character_roster,
            writing_guidance,
            compact_characters,
            compact_world_facts,
            compact_timeline,
        )
        pressure = base.model_copy(
            update={
                "title": f"{base.title}·限时选择",
                "conflict": f"{base.conflict}；原定退路在行动开始后失效，主角必须舍弃一项已有成果",
                "turning_point": "对手先一步利用了公开信息，主角只能在救人、保住资源和隐瞒线索之间选择两项",
                "critical_choice": "主动舍弃一项已经到手的成果，换取避免更大损失的唯一窗口",
                "scenes": [
                    "从上一章尚未解决的动作直接起笔，异常立即造成可见后果",
                    "主角按原计划行动，却发现退路已被对手或环境切断",
                    "不同人物提出互斥方案，各自都隐瞒一项现实利益",
                    "主角作出不可兼得的选择并当场付出代价",
                    "阶段成果保住一半，新证据证明威胁比预想更近",
                ],
            }
        )
        relationship = base.model_copy(
            update={
                "title": f"{base.title}·错误盟友",
                "conflict": f"{base.conflict}；最可信的协作者拒绝执行关键步骤，理由与其既有目标直接冲突",
                "turning_point": "一次看似背叛的行动实际挡住了更坏结果，但也暴露双方无法回避的利益分歧",
                "critical_choice": "主角必须决定公开一部分底牌换取合作，还是独自承担更高风险",
                "scenes": [
                    "承接上一章结尾，由一个人物的反常行动打断既定安排",
                    "主角追查反常原因，发现协作者掌握的信息并不完整",
                    "双方在具体资源和责任上发生无法用口号化解的争执",
                    "外部威胁迫使两人带着分歧合作，并留下可追踪的关系裂痕",
                    "行动取得有限回报，但新的不信任改变下一章的人物站位",
                ],
            }
        )
        return ChapterPlanBatch(candidates=[base, pressure, relationship])

    def select_chapter_plan(
        self,
        candidates: list[ChapterPlan],
        previous_chapter_excerpt: str = "",
    ) -> ChapterPlanSelection:
        evaluations = [
            PlanCandidateEvaluation(
                index=index,
                causality=88 if index == 1 else 80,
                conflict_pressure=92 if index == 1 else 82,
                character_choice=90 if index == 1 else 84,
                novelty=84 if index == 1 else 78,
                continuity=90 if previous_chapter_excerpt else 82,
                payoff=88 if index == 1 else 83,
                weaknesses=[] if index == 1 else ["冲突压力略低于候选二"],
            )
            for index, _ in enumerate(candidates)
        ]
        return ChapterPlanSelection(
            selected_index=1,
            rationale="候选二的选择不可兼得、代价可见，且最容易由上一章未完成动作自然触发。",
            evaluations=evaluations,
        )

    def write_chapter(
        self,
        request: NovelCreateRequest,
        brief: CreativeBrief,
        plan: ChapterPlan,
        protagonist_name: str,
        research_notes: list[str],
        character_roster: list[CharacterRecord] | None = None,
        genre_prose_card: str = "",
        compact_characters: list[str] | None = None,
        previous_chapter_excerpt: str = "",
    ) -> str:
        seed = int(hashlib.sha256(f"{brief.selected_title}:{plan.number}".encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        locations = ["仓库外的坡道", "临时会议室", "停电的街口", "半开的卷帘门后", "屋顶水箱旁"]
        sounds = ["远处金属撞击声", "备用电源的低鸣", "风里断续的警报", "雨水敲击棚顶", "无线电的杂音"]
        actions = ["核对清单", "重新划定路线", "检查封条", "分配工具", "记录每一笔消耗"]
        supporting_names = [
            update.name for update in plan.character_updates if update.name != protagonist_name
        ]
        companion = supporting_names[0] if supporting_names else "同行者"
        paragraphs: list[str] = [
            plan.title,
            (
                f"{rng.choice(sounds)}把天色切成几段。{protagonist_name}站在{rng.choice(locations)}，"
                f"没有急着下结论。他先看了一眼昨夜留下的标记，再把今天唯一必须完成的事写在纸上："
                f"{plan.objective}。眼下没有无限资源，也没有可以重来的机会。"
            ),
        ]
        notes = "；".join(research_notes[:2]) if research_notes else "外部资料尚未接入，涉及现实细节只采用保守描述"
        for index, scene in enumerate(plan.scenes, start=1):
            place = rng.choice(locations)
            action = rng.choice(actions)
            paragraphs.extend(
                [
                    (
                        f"第{index}步从{place}开始。{scene}。{protagonist_name}让所有人先{action}，"
                        "把能够确认的事实、仍然未知的风险和最坏结果分开写。有人认为这太慢，"
                        "可当第一组数字落在纸面上，原本模糊的争论立刻有了边界。"
                    ),
                    (
                        f"场景{index}的阻力比预想来得早：{plan.conflict}。质疑声从人群后方冒出来，"
                        f"{protagonist_name}没有许诺轻易的胜利，只把方案拆成两个能在一小时内验证的小动作。"
                        f"{companion}提出了不同意见。若第一步失败，他们仍保留撤退路线；"
                        "若成功，成果归所有参与者共同记录。"
                    ),
                    (
                        f"场景{index}推进到一半，{rng.choice(sounds)}忽然停了。安静反而暴露了异常。"
                        f"{plan.turning_point}。{protagonist_name}顺着痕迹复核时间和位置，"
                        "确认这不是直觉带来的巧合。他把新线索记入日志，却没有让未经验证的猜测变成命令。"
                    ),
                    (
                        f"场景{index}的代价随成果一起出现。为了守住刚取得的进展，他们放弃了另一项看似诱人的机会。"
                        f"{protagonist_name}清点余量时特意写下依据：{notes}。"
                        "这份克制没有带来欢呼，却让队伍第一次相信，今天的选择明天仍能被解释和追溯。"
                    ),
                ]
            )
        paragraphs.extend(
            [
                (
                    "傍晚前，计划终于形成了可以触摸的结果。它不够漂亮，也远非终点，"
                    f"但确实完成了本章目标：{plan.objective}。有人开始主动补全记录，"
                    "有人重新计算明天的消耗，原先彼此冲突的意见被压缩成了几个等待验证的问题。"
                ),
                (
                    f"{protagonist_name}最后一次巡视现场时，在旧档案的夹层里发现一行被刻意擦淡的编号。"
                    f"它与今天的异常完全对应。{plan.hook}。他合上记录本，"
                    "没有把这个发现告诉所有人，只在下一页写下了一个新的时间和地点。"
                ),
            ]
        )
        content = "\n\n".join(paragraphs)
        cycle = 1
        while len(content) < request.chapter_target_chars:
            detail = (
                f"补充记录{cycle}：{protagonist_name}把本轮行动拆成输入、消耗、结果和待验证项。"
                f"第{plan.number}章形成的成果只能解决眼前问题，不能越过既定世界规则。"
                "他要求每个人只报告亲眼看见的变化，并把传闻单独标注。这个看似笨拙的流程，"
                "让下一轮决策不必依赖任何人的完美记忆，也避免草率判断成为新的隐患。"
            )
            content += "\n\n" + detail
            cycle += 1
        return content

    def deslop_chapter(
        self,
        content: str,
        plan: ChapterPlan,
        genre_prose_card: str,
        skill_instructions: str,
    ) -> str:
        """Deterministic offline approximation of the vendored deslop contract."""
        replacements = {
            "深吸一口气": "把话咽了回去",
            "眼中闪过一丝": "垂下眼，",
            "嘴角勾起一抹": "笑了一声，",
            "他终于明白": "他把目光落回眼前",
            "她终于明白": "她把目光落回眼前",
            "——": "，",
            "—": "，",
        }
        polished = content
        for source, target in replacements.items():
            polished = polished.replace(source, target)
        return polished

    def extract_character_memories(
        self,
        brief: CreativeBrief,
        chapter_number: int,
        content: str,
        character_roster: list[CharacterRecord] | None = None,
    ) -> CharacterMemoryBatch:
        updates = []
        for character in character_roster or []:
            if character.name not in content:
                continue
            updates.append(
                CharacterMemoryUpdate(
                    name=character.name,
                    role=character.role,
                    last_result=f"在第{chapter_number}章参与剧情",
                )
            )
        return CharacterMemoryBatch(characters=updates)

    def review_continuity(
        self,
        context: ContinuityContext,
        skill_instructions: str,
    ) -> ReviewReport:
        """Offline mode still invokes the Skill contract without an external model."""
        return ReviewReport(
            decision=ReviewDecision.PASS,
            score=100,
            checks={"semantic": 100},
        )

    def review_readability(self, context: ReadabilityContext) -> ReviewReport:
        """Offline mode keeps the quality-review node deterministic and side-effect free."""
        dimensions = {
            "opening_hook": 90.0,
            "scene_causality": 90.0,
            "conflict_escalation": 90.0,
            "character_agency": 90.0,
            "dialogue_voice": 90.0,
            "showing_specificity": 90.0,
            "reader_payoff": 90.0,
            "ending_hook": 90.0,
        }
        return ReviewReport(
            decision=ReviewDecision.PASS,
            score=90,
            checks=dimensions,
        )

    def revise_chapter(self, content: str, instructions: list[str], target_chars: int) -> str:
        revised = content
        for instruction in instructions:
            if instruction.startswith("删除禁止项："):
                term = instruction.split("：", 1)[1]
                revised = revised.replace(term, "[已按创作约束修订]")
        while len(revised) < target_chars:
            revised += (
                "\n\n修订补记：人物重新核对了行动依据、时间顺序和资源余量。"
                "新增信息被标明来源，尚未确认的判断没有写入正式结论。"
            )
        return revised

    @staticmethod
    def _protagonist_name(request: NovelCreateRequest) -> str:
        digest = int(hashlib.md5(request.idea.encode()).hexdigest()[:4], 16)
        surnames = ["林", "陈", "周", "顾", "沈", "许", "陆", "江"]
        given = ["砚", "川", "宁", "序", "野", "衡", "舟", "澈"]
        return surnames[digest % len(surnames)] + given[(digest // 7) % len(given)]


def build_gateway(
    mode: str,
    provider: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout_seconds: float = 120.0,
    max_retries: int = 2,
    reasoning_budget_factor: float = 3.0,
) -> LLMGateway:
    if mode == "offline" or provider == "disabled":
        return OfflineLLMGateway()
    if provider == "deepseek":
        from novel_agent.adapters_remote import DeepSeekLLMGateway

        return DeepSeekLLMGateway(
            api_key=api_key or "",
            base_url=base_url or "https://api.deepseek.com",
            model=model or "deepseek-v4-pro",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            reasoning_budget_factor=reasoning_budget_factor,
        )
    return DisabledRemoteGateway(provider=provider)


def build_search_adapter(
    mode: str,
    provider: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float = 30.0,
    max_results: int = 3,
) -> SearchAdapter:
    if mode == "offline" or provider == "disabled":
        return DisabledSearchAdapter()
    if provider == "tavily":
        from novel_agent.adapters_remote import TavilySearchAdapter

        return TavilySearchAdapter(
            api_key=api_key or "",
            base_url=base_url or "https://api.tavily.com",
            timeout_seconds=timeout_seconds,
            max_results=max_results,
        )
    raise APIConfigurationRequired(f"不支持的搜索供应商：{provider}")


def build_embedding_gateway(
    mode: str,
    provider: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout_seconds: float = 60.0,
) -> EmbeddingGateway:
    if mode == "offline" or provider == "disabled":
        return DisabledEmbeddingGateway()
    if provider in {"siliconflow", "openai-compatible"}:
        from novel_agent.adapters_remote import OpenAICompatibleEmbeddingGateway

        default_url = "https://api.siliconflow.cn/v1" if provider == "siliconflow" else ""
        return OpenAICompatibleEmbeddingGateway(
            provider=provider,
            api_key=api_key or "",
            base_url=base_url or default_url,
            model=model or "BAAI/bge-m3",
            timeout_seconds=timeout_seconds,
        )
    raise APIConfigurationRequired(f"不支持的 Embedding 供应商：{provider}")
