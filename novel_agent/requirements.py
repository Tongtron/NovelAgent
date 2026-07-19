from __future__ import annotations

from novel_agent.models import NovelCreateRequest
from novel_agent.tags import TAG_CATALOG


class RequirementConflict(ValueError):
    def __init__(self, conflicts: list[str]):
        self.conflicts = conflicts
        super().__init__("；".join(conflicts))


def validate_requirements(request: NovelCreateRequest) -> None:
    catalog = TAG_CATALOG[request.audience_channel]
    values = set(
        [request.genre, request.romance or ""]
        + request.experiences
        + request.elements
        + request.protagonist_tags
        + request.must_have
        + request.nice_to_have
        + request.exclude
    )
    conflicts: list[str] = []
    if request.genre not in catalog["genres"]:
        conflicts.append(
            f"“{request.genre}”不属于{request.audience_channel}当前可选主类型"
        )
    incompatible = [
        ("无感情线", "先婚后爱", "“无感情线”和“先婚后爱”不能同时成立"),
        ("无感情线", "单女主", "“无感情线”和“单女主”需要选择其一"),
        ("不要系统", "系统", "排除“系统”后不能再把“系统”作为主角设定"),
        ("严格历史纪实", "随意改变重大历史结果", "严格纪实与随意改写历史结果冲突"),
    ]
    for left, right, message in incompatible:
        if left in values and right in values:
            conflicts.append(message)
    overlap = set(request.must_have) & set(request.exclude)
    conflicts.extend(f"“{item}”同时出现在必须包含和明确不要中" for item in sorted(overlap))
    if conflicts:
        raise RequirementConflict(conflicts)
