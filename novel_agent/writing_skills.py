from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from novel_agent.models import (
    ChapterPlan,
    IssueSeverity,
    ProseAuditReport,
    ProseFinding,
    ReviewDecision,
    ReviewIssue,
    ReviewReport,
    SceneBeat,
)


OH_STORY_REPOSITORY = "worldwonderer/oh-story-claudecode"
OH_STORY_COMMIT = "964d6bfdb7b78b225591e4b35bfa00d245d4f9a2"
REQUIRED_SKILLS = ("story-long-write", "story-deslop")


class WritingSkillsConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WritingSkillDefinition:
    name: str
    version: str
    path: Path
    instructions: str


@dataclass(frozen=True, slots=True)
class GenreProseCard:
    name: str
    aliases: tuple[str, ...]
    confidence: str
    path: Path
    instructions: str

    def prompt_excerpt(self) -> str:
        headings = (
            "正文提示词",
            "冲突发动机",
            "爽点与情绪释放",
            "正文落点",
            "前中后期打法",
            "本章取舍",
            "禁止漂移",
        )
        sections = [
            _markdown_section(self.instructions, f"## {heading}", "## ")
            for heading in headings
        ]
        useful = [section.strip() for section in sections if section.strip()]
        return "\n\n".join(useful)[:4000]


@dataclass(slots=True)
class OhStoryWritingRuntime:
    enabled: bool
    root: Path | None = None
    definitions: dict[str, WritingSkillDefinition] = field(default_factory=dict)
    cards: tuple[GenreProseCard, ...] = ()
    banned_words: str = ""
    anti_ai_writing: str = ""
    source_repository: str = OH_STORY_REPOSITORY
    source_commit: str = OH_STORY_COMMIT

    @classmethod
    def disabled(cls) -> "OhStoryWritingRuntime":
        return cls(enabled=False)

    @classmethod
    def load(
        cls,
        project_root: Path,
        *,
        enabled: bool = True,
    ) -> "OhStoryWritingRuntime":
        if not enabled:
            return cls.disabled()
        root = project_root / "vendor" / "oh-story" / "skills"
        definitions = {
            name: _load_skill(root / name / "SKILL.md", expected_name=name)
            for name in REQUIRED_SKILLS
        }
        card_root = root / "story-long-write" / "references" / "genre-prose-cards"
        cards = tuple(_load_genre_card(path) for path in sorted(card_root.glob("*.md")))
        if not cards:
            raise WritingSkillsConfigurationError("oh-story 题材正文提示卡缺失")
        deslop_refs = root / "story-deslop" / "references"
        banned_words = _read_required(deslop_refs / "banned-words.md")
        anti_ai_writing = _read_required(deslop_refs / "anti-ai-writing.md")
        return cls(
            enabled=True,
            root=root,
            definitions=definitions,
            cards=cards,
            banned_words=banned_words,
            anti_ai_writing=anti_ai_writing,
        )

    def readiness(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "provider": OH_STORY_REPOSITORY,
            "version": self.source_commit[:12] if self.enabled else "disabled",
            "skills": sorted(self.definitions),
            "genre_cards": len(self.cards),
        }

    def genre_card(self, genre: str, audience_channel: str = "男频") -> GenreProseCard | None:
        if not self.enabled:
            return None
        normalized = _normalize_genre(genre)
        candidates: list[tuple[int, GenreProseCard]] = []
        for card in self.cards:
            terms = (card.name, *card.aliases)
            score = max((_match_score(normalized, _normalize_genre(term)) for term in terms), default=0)
            if score:
                candidates.append((score, card))
        if candidates:
            return max(candidates, key=lambda item: (item[0], item[1].confidence == "high"))[1]

        fallbacks = (
            (("末世", "废土", "灾变", "科幻"), "科幻末世"),
            (("悬疑", "推理"), "女频悬疑" if audience_channel == "女频" else "悬疑脑洞"),
            (("都市",), "都市日常"),
            (("玄幻",), "传统玄幻"),
            (("仙侠", "修仙"), "东方仙侠"),
            (("历史",), "历史古代"),
            (("言情",), "职场婚恋" if "现代" in genre else "古风世情"),
            (("游戏", "电竞", "体育"), "游戏体育"),
        )
        for needles, card_name in fallbacks:
            if any(_normalize_genre(needle) in normalized for needle in needles):
                return next((card for card in self.cards if card.name == card_name), None)
        return None

    def chapter_guidance(self, genre: str, audience_channel: str) -> dict[str, str]:
        if not self.enabled:
            return {}
        skill = self.definitions["story-long-write"]
        card = self.genre_card(genre, audience_channel)
        core = _markdown_section(skill.instructions, "## 核心方法", "---")
        detailed = _markdown_section(skill.instructions, "#### 细纲（全书每章）", "**大纲锁定**")
        return {
            "skill": skill.name,
            "source": f"{self.source_repository}@{self.source_commit}",
            "planning_method": f"{core}\n\n{detailed}"[:12000],
            "genre_card_name": card.name if card else "通用长篇",
            "genre_card_confidence": card.confidence if card else "fallback",
            "genre_prose_card": card.prompt_excerpt() if card else "",
        }

    def deslop_guidance(self) -> str:
        if not self.enabled:
            return ""
        skill = self.definitions["story-deslop"]
        core = _markdown_section(skill.instructions, "## 核心哲学", "---")
        gates = [
            _markdown_section(skill.instructions, f"#### 门禁 {letter}", "#### ")
            for letter in "ABCDEFG"
        ]
        quick = _markdown_section(self.anti_ai_writing, "## 快速自检口诀", "---")
        provenance = (
            f"# story-deslop\nSource: {self.source_repository}@{self.source_commit}\n"
            "NovelAgent 只采用语言编辑规则，不执行 Skill 中的命令、文件或 Agent 操作。"
        )
        return "\n\n".join([provenance, core, *gates, quick])[:18000]

    def normalize_plan(
        self,
        plan: ChapterPlan,
        *,
        total_chapters: int,
        target_chars: int,
    ) -> ChapterPlan:
        if not self.enabled:
            return plan
        ratio = plan.number / max(total_chapters, 1)
        stage = (
            "开篇期" if ratio <= 0.15 else
            "发展期" if ratio <= 0.75 else
            "高潮期" if ratio <= 0.95 else
            "收尾期"
        )
        scene_count = max(1, len(plan.scenes))
        base_budget = max(40, target_chars // scene_count)
        beats = plan.scene_beats or [
            SceneBeat(
                sequence=index,
                scene=scene,
                purpose=(
                    "建立目标与期待" if index == 1 else
                    "释放本章主要回报并制造新期待" if index == scene_count else
                    "升级阻力并推动选择"
                ),
                emotional_shift=(
                    "好奇 → 期待" if index == 1 else
                    "压力 → 阶段满足" if index == scene_count else
                    "期待 → 紧张"
                ),
                intensity="dense" if index == scene_count else "normal",
                target_chars=(
                    target_chars - base_budget * (scene_count - 1)
                    if index == scene_count else base_budget
                ),
            )
            for index, scene in enumerate(plan.scenes, start=1)
        ]
        relationship_changes = dict(plan.relationship_changes)
        if not relationship_changes:
            for update in plan.character_updates:
                relationship_changes.update(update.relationship_changes)
        updates = {
            "story_stage": plan.story_stage or stage,
            "plot_unit": plan.plot_unit or f"剧情单元-{(plan.number - 1) // 10 + 1}",
            "chapter_position": plan.chapter_position or "推进",
            "target_emotion": plan.target_emotion or "不确定与压力 → 获得阶段性掌控感",
            "reader_payoff": plan.reader_payoff or plan.objective,
            "new_expectation": plan.new_expectation or plan.hook,
            "protagonist_goal": plan.protagonist_goal or plan.objective,
            "critical_choice": plan.critical_choice or plan.turning_point,
            "opening_hook": plan.opening_hook or (plan.scenes[0] if plan.scenes else plan.conflict),
            "plotline_progress": plan.plotline_progress or {"主线": plan.objective},
            "relationship_changes": relationship_changes,
            "information_gap": plan.information_gap or [plan.turning_point],
            "scene_beats": beats,
        }
        return plan.model_copy(update=updates)

    def audit_prose(self, content: str) -> ProseAuditReport:
        if not self.enabled:
            return ProseAuditReport()
        body = content.split("\n", 1)[1] if "\n" in content else content
        findings: list[ProseFinding] = []

        blocking_patterns = (
            ("not-is-comparison", r"不是[^。！？\n]{1,36}[，,](?:而)?是", "否定铺垫后肯定翻转"),
            ("negation-parade", r"没有[^。！？\n]{1,32}[，,]没有[^。！？\n]{1,32}(?:只是|而是)", "连续否定后翻转"),
            ("voice-contrast", r"声音[^。！？\n]{0,16}(?:不大|不高|平直)[^。！？\n]{0,24}(?:却|但)", "模板化声线反差"),
            ("em-dash", r"——|—|--", "正文使用破折号或双连字符"),
        )
        for code, pattern, message in blocking_patterns:
            match = re.search(pattern, body)
            if match:
                findings.append(
                    ProseFinding(
                        code=code,
                        severity=IssueSeverity.MAJOR,
                        message=message,
                        suggestion="保留剧情事实，删掉模板句式，改用角色动作、对话或具体后果表达",
                        evidence=match.group(0)[:120],
                    )
                )

        tail = body[-320:]
        trailer = re.search(
            r"(?:他不知道的是|她不知道的是|才刚刚开始|即将拉开序幕|更大的风暴|命运.{0,8}(?:齿轮|棋局|獠牙))",
            tail,
        )
        if trailer:
            findings.append(
                ProseFinding(
                    code="trailer-ending",
                    severity=IssueSeverity.MAJOR,
                    message="章尾使用总结或预告式 AI 收束",
                    suggestion="改用本章已经建立的动作、对话、物件或未解决问题收尾",
                    evidence=trailer.group(0),
                )
            )

        tier_one = (
            "眼中闪过", "嘴角勾起", "心中涌起", "映入眼帘", "心中暗道",
            "深吸一口气", "不由自主", "不容置疑", "这一刻终于明白",
        )
        hits = [(term, len(re.findall(re.escape(term), body))) for term in tier_one]
        total_hits = sum(count for _, count in hits)
        for term, count in hits:
            if count:
                findings.append(
                    ProseFinding(
                        code="cliche",
                        severity=IssueSeverity.MINOR,
                        message=f"高频 AI 套词“{term}”出现 {count} 次",
                        suggestion="结合上下文删除，或用角色当下的具体动作、物件和后果表达",
                        evidence=term,
                    )
                )

        chars = max(1, len(body))
        weak_count = sum(body.count(term) for term in ("微微", "淡淡", "缓缓", "轻轻"))
        weak_per_k = weak_count * 1000 / chars
        if weak_per_k > 3:
            findings.append(
                ProseFinding(
                    code="weak-adverb-density",
                    severity=IssueSeverity.MINOR,
                    message=f"弱化副词密度偏高（{weak_per_k:.1f}/千字）",
                    suggestion="只保留确有语气功能的少量副词，其余改成明确动作",
                )
            )

        major_count = sum(item.severity in {IssueSeverity.BLOCKER, IssueSeverity.MAJOR} for item in findings)
        score = max(0.0, 100.0 - major_count * 18 - (len(findings) - major_count) * 3)
        level = "heavy" if major_count >= 3 or total_hits > chars / 1000 * 15 else "medium" if major_count else "light" if findings else "clean"
        return ProseAuditReport(
            level=level,
            score=score,
            findings=findings,
            metrics={
                "characters": float(chars),
                "tier_one_hits": float(total_hits),
                "weak_adverbs_per_1000": round(weak_per_k, 2),
            },
        )

    @staticmethod
    def as_review_report(audit: ProseAuditReport) -> ReviewReport:
        issues = [
            ReviewIssue(
                category="prose_ai_style",
                severity=finding.severity,
                message=finding.message,
                suggestion=finding.suggestion,
                evidence=[finding.evidence] if finding.evidence else [],
            )
            for finding in audit.findings
        ]
        has_major = any(
            issue.severity in {IssueSeverity.BLOCKER, IssueSeverity.MAJOR}
            for issue in issues
        )
        return ReviewReport(
            decision=ReviewDecision.REVISE if has_major else ReviewDecision.PASS,
            score=audit.score,
            issues=issues,
            checks={"prose_naturalness": audit.score},
        )


def _read_required(path: Path) -> str:
    if not path.is_file():
        raise WritingSkillsConfigurationError(f"缺少 oh-story 文件：{path}")
    return path.read_text(encoding="utf-8")


def _load_skill(path: Path, *, expected_name: str) -> WritingSkillDefinition:
    text = _read_required(path)
    frontmatter = _frontmatter(text)
    name = frontmatter.get("name", "")
    if name != expected_name:
        raise WritingSkillsConfigurationError(
            f"oh-story Skill 名称不匹配：期望 {expected_name}，实际 {name or '空'}"
        )
    return WritingSkillDefinition(
        name=name,
        version=frontmatter.get("version", "unknown"),
        path=path,
        instructions=text,
    )


def _load_genre_card(path: Path) -> GenreProseCard:
    text = _read_required(path)
    metadata = _frontmatter(text)
    aliases = metadata.get("aliases", "").strip("[]")
    return GenreProseCard(
        name=metadata.get("genre", path.stem),
        aliases=tuple(item.strip().strip("'\"") for item in aliases.split(",") if item.strip()),
        confidence=metadata.get("confidence", "unknown"),
        path=path,
        instructions=text,
    )


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        raise WritingSkillsConfigurationError("oh-story Markdown 缺少 frontmatter")
    _, raw, _ = text.split("---", 2)
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _markdown_section(text: str, start: str, end: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        return ""
    content_start = start_index + len(start)
    end_index = text.find(end, content_start)
    if end_index < 0:
        end_index = len(text)
    return text[start_index:end_index].strip()


def _normalize_genre(value: str) -> str:
    return re.sub(r"[\s/·、+_-]+", "", value.lower())


def _match_score(requested: str, candidate: str) -> int:
    if not requested or not candidate:
        return 0
    if requested == candidate:
        return 100 + len(candidate)
    if candidate in requested:
        return 70 + len(candidate)
    if requested in candidate and len(requested) >= 2:
        return 50 + len(requested)
    return 0
