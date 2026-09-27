from __future__ import annotations

from dataclasses import dataclass

from novel_agent.models import ProjectStatus
from novel_agent.service import NovelAgentService


@dataclass(slots=True)
class LocalScheduler:
    """One-shot local scheduler; safe precursor to a durable Temporal workflow."""

    service: NovelAgentService

    def tick(self, per_project_limit: int = 1) -> dict[str, object]:
        generated = 0
        failures: list[dict[str, str]] = []
        for project in self.service.repository.list_projects():
            if project.status != ProjectStatus.ACTIVE or not project.confirmed:
                continue
            try:
                generated += len(self.service.fill_reserve(project.id, per_project_limit))
            except Exception as exc:
                failures.append({"project_id": project.id, "error": type(exc).__name__})
        return {"generated": generated, "failures": failures}

