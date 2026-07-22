"""Token-budgeted long-term-memory context construction.

SQLite remains authoritative. This module only chooses and compacts the
records sent to a model; it never deletes or claims to persist evicted data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from novel_agent.models import CharacterRecord, WorldFactRecord


_CJK = re.compile(r"[一-鿿㐀-䶿豈-﫿]")


@dataclass(slots=True)
class MemoryManager:
    hot_budget_tokens: int = 6000
    char_per_token: float = 1.5
    character_chapter_gap: int = 10
    world_fact_chapter_gap: int = 20
    timeline_chapter_gap: int = 15
    research_chapter_gap: int = 5
    research_reliability_min: float = 0.5
    _budget_characters: float = field(default=0.40, init=False)
    _budget_world_facts: float = field(default=0.25, init=False)
    _budget_timeline: float = field(default=0.20, init=False)
    _budget_research: float = field(default=0.15, init=False)

    # ------------------------------------------------------------------
    # Token estimation
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_tokens(text: str) -> int:
        if not text:
            return 0
        cjk = len(_CJK.findall(text))
        other = len(text) - cjk
        return max(1, int(cjk / 1.5 + other / 4.0))

    # ------------------------------------------------------------------
    # Compact formatters
    # ------------------------------------------------------------------

    def format_character_compact(
        self, char: CharacterRecord, current_chapter: int
    ) -> str:
        state = char.current_state or {}
        last_seen = int(state.get("last_seen_chapter") or 0)
        gap = max(0, current_chapter - last_seen) if last_seen else 0
        role = char.role or "supporting"
        importance = state.get("importance", "major")

        parts: list[str] = []
        parts.append(f"[角色={char.name} | {role} | {importance}]")

        facts = char.immutable_facts or []
        max_facts = 4 if role == "protagonist" else 3
        relevant = [
            f for f in facts[:max_facts]
            if not f.startswith("主角姓名为")
        ]
        if relevant:
            parts.append("背景: " + "; ".join(relevant))

        goal = str(state.get("current_goal") or state.get("goal") or "")
        if goal.strip():
            parts.append(f"目标: {goal[:200]}")

        result = str(state.get("last_result") or "")
        if result.strip():
            parts.append(f"最近: {result[:150]}")

        location = str(state.get("location") or "")
        if location.strip():
            parts.append(f"位置: {location[:80]}")

        knowledge = state.get("knowledge")
        if isinstance(knowledge, list) and knowledge:
            items = [str(k)[:80] for k in knowledge[:5]]
            parts.append("已知: " + "; ".join(items))

        relationships = state.get("relationships")
        if isinstance(relationships, dict) and relationships:
            rels = [
                f"{k}:{str(v)[:40]}"
                for k, v in list(relationships.items())[:5]
            ]
            parts.append("关系: " + ", ".join(rels))

        if gap > 0:
            parts.append(f"距当前: {gap}章")

        return "\n".join(parts)

    @staticmethod
    def format_world_fact_compact(fact: WorldFactRecord) -> str:
        lock = " [已锁定]" if fact.locked else ""
        chapter = (
            f" (第{fact.evidence_chapter}章确立)"
            if fact.evidence_chapter
            else ""
        )
        return f"[规则={fact.category}]{lock}{chapter} {fact.statement}"

    @staticmethod
    def format_timeline_compact(
        event: dict[str, Any], current_chapter: int
    ) -> str:
        cn = event.get("chapter_number", 0)
        gap = max(0, current_chapter - cn)
        gap_str = f" ({gap}章前)" if gap > 0 else ""
        text = str(event.get("event", ""))[:200]
        return f"第{cn}章{gap_str}: {text}"

    @staticmethod
    def format_research_compact(source: dict[str, Any]) -> str:
        title = source.get("title") or source.get("domain") or "网页来源"
        reliability = float(source.get("reliability", 0))
        verified = "可交叉核验" if source.get("verified") else "单一来源"
        excerpt = str(source.get("excerpt") or "")[:200]
        lines = [
            f"[研究={title} | 可信度={reliability:.2f} | {verified}]",
        ]
        if excerpt:
            lines.append(f"摘录: {excerpt}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Context building
    # ------------------------------------------------------------------

    def build_context(
        self,
        characters: list[CharacterRecord],
        world_facts: list[WorldFactRecord],
        timeline: list[dict[str, Any]],
        research_sources: list[dict[str, Any]],
        current_chapter: int,
    ) -> dict[str, list[str]]:
        def last_seen(character: CharacterRecord) -> int:
            return int((character.current_state or {}).get("last_seen_chapter") or 0)

        protagonists = [item for item in characters if item.role == "protagonist"]
        supporting = [item for item in characters if item.role != "protagonist"]
        supporting.sort(
            key=lambda item: (
                0
                if last_seen(item)
                and current_chapter - last_seen(item) <= self.character_chapter_gap
                else 1,
                -last_seen(item),
                0 if (item.current_state or {}).get("importance") == "core" else 1,
                item.name,
            )
        )

        locked_facts = [item for item in world_facts if item.locked]
        unlocked_facts = [item for item in world_facts if not item.locked]
        unlocked_facts.sort(
            key=lambda item: (
                0
                if item.evidence_chapter
                and current_chapter - item.evidence_chapter <= self.world_fact_chapter_gap
                else 1,
                -(item.evidence_chapter or 0),
            )
        )

        # Recent events are more useful when a category cannot fit in full.
        ordered_timeline = sorted(
            timeline,
            key=lambda item: (
                0
                if current_chapter - int(item.get("chapter_number") or 0)
                <= self.timeline_chapter_gap
                else 1,
                -int(item.get("chapter_number") or 0),
            ),
        )
        eligible_research = [
            item
            for item in research_sources
            if item.get("verified")
            or float(item.get("reliability") or 0) >= self.research_reliability_min
        ]
        ordered_research = sorted(
            eligible_research,
            key=lambda item: (
                0
                if not item.get("_chapter_number")
                or current_chapter - int(item.get("_chapter_number") or 0)
                <= self.research_chapter_gap
                else 1,
                -int(bool(item.get("verified"))),
                -float(item.get("reliability") or 0),
                -int(item.get("_chapter_number") or 0),
            ),
        )

        # --- Format and trim to budget ---
        char_limit = int(self.hot_budget_tokens * self._budget_characters)
        fact_limit = int(self.hot_budget_tokens * self._budget_world_facts)
        timeline_limit = int(self.hot_budget_tokens * self._budget_timeline)
        research_limit = int(self.hot_budget_tokens * self._budget_research)

        def trim(items: list[str], limit: int) -> list[str]:
            kept: list[str] = []
            used = 0
            for item in items:
                cost = self.estimate_tokens(item)
                if used + cost > limit:
                    continue
                kept.append(item)
                used += cost
            return kept

        def trim_with_required(
            required: list[str], optional: list[str], limit: int
        ) -> list[str]:
            # Protagonists and locked rules are correctness constraints. Keep
            # them even when their combined size exceeds the nominal budget.
            used = sum(self.estimate_tokens(item) for item in required)
            return [*required, *trim(optional, max(0, limit - used))]

        compact_chars = trim_with_required(
            [self.format_character_compact(item, current_chapter) for item in protagonists],
            [self.format_character_compact(item, current_chapter) for item in supporting],
            char_limit,
        )
        compact_facts = trim_with_required(
            [self.format_world_fact_compact(item) for item in locked_facts],
            [self.format_world_fact_compact(item) for item in unlocked_facts],
            fact_limit,
        )
        compact_timeline = trim(
            [
                self.format_timeline_compact(ev, current_chapter)
                for ev in ordered_timeline
            ],
            timeline_limit,
        )
        compact_research = trim(
            [self.format_research_compact(item) for item in ordered_research],
            research_limit,
        )

        return {
            "compact_characters": compact_chars,
            "compact_world_facts": compact_facts,
            "compact_timeline": compact_timeline,
            "compact_research": compact_research,
        }
