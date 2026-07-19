from __future__ import annotations

import json
import mimetypes
import re
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from novel_agent.config import Settings
from novel_agent.models import BriefRegenerateRequest, CreativeBrief, NovelCreateRequest
from novel_agent.repository import RepositoryError, SQLiteRepository
from novel_agent.requirements import RequirementConflict
from novel_agent.service import NovelAgentService
from novel_agent.tags import public_tag_catalog
from novel_agent.workflow import WorkflowBlocked


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


@dataclass(slots=True)
class AppContext:
    settings: Settings
    service: NovelAgentService
    repository: SQLiteRepository
    web_root: Path


class NovelAgentHandler(BaseHTTPRequestHandler):
    server_version = "NovelAgent/0.1"
    context: AppContext

    def log_message(self, format: str, *args: object) -> None:
        print(f"[api] {self.address_string()} - {format % args}")

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        try:
            path = self.path.split("?", 1)[0]
            if path == "/api/health":
                self._send_json(
                    {
                        "status": "ok",
                        "version": "0.1.0",
                        "mode": self.context.settings.mode,
                        "database": "ready",
                        "apis": self.context.settings.api_readiness(),
                    }
                )
                return
            if path == "/api/tags":
                self._send_json(public_tag_catalog())
                return
            if path == "/api/novels":
                self._send_json({"items": self.context.repository.list_projects()})
                return
            match = re.fullmatch(r"/api/novels/([^/]+)", path)
            if match:
                self._send_json(self.context.service.project_detail(match.group(1)))
                return
            match = re.fullmatch(r"/api/novels/([^/]+)/chapters", path)
            if match:
                self._send_json({"items": self.context.repository.list_chapters(match.group(1))})
                return
            match = re.fullmatch(r"/api/novels/([^/]+)/characters", path)
            if match:
                self._send_json({"items": self.context.repository.list_characters(match.group(1))})
                return
            match = re.fullmatch(r"/api/novels/([^/]+)/world", path)
            if match:
                project_id = match.group(1)
                self._send_json(
                    {
                        "facts": self.context.repository.list_world_facts(project_id),
                        "timeline": self.context.repository.list_timeline(project_id),
                    }
                )
                return
            match = re.fullmatch(r"/api/novels/([^/]+)/research", path)
            if match:
                self._send_json(
                    {"items": self.context.repository.list_research_sources(match.group(1))}
                )
                return
            match = re.fullmatch(r"/api/novels/([^/]+)/brief-versions", path)
            if match:
                self._send_json(
                    {"items": self.context.repository.list_brief_versions(match.group(1))}
                )
                return
            match = re.fullmatch(r"/api/runs/([^/]+)/events", path)
            if match:
                self._send_sse(self.context.repository.list_events(match.group(1)))
                return
            if path == "/api/settings":
                self._send_json(
                    {
                        "runtime": self.context.settings.api_readiness(),
                        "preferences": self.context.repository.list_settings(),
                    }
                )
                return
            if path == "/api/publishing":
                self._send_json(
                    {
                        "provider": self.context.settings.publisher_provider,
                        "mode": "mock-only",
                        "real_publish_enabled": False,
                        "message": "真实番茄发布适配器未实现，避免账号与平台风险。",
                    }
                )
                return
            self._serve_static(path)
        except Exception as exc:
            self._handle_error(exc)

    def do_POST(self) -> None:
        try:
            path = self.path.split("?", 1)[0]
            payload = self._read_json()
            if path == "/api/novels":
                request = NovelCreateRequest.model_validate(payload)
                self._send_json(self.context.service.create_novel(request), HTTPStatus.CREATED)
                return
            match = re.fullmatch(r"/api/novels/([^/]+)/(confirm|pause|resume|recover)", path)
            if match:
                project_id, action = match.groups()
                method = {
                    "confirm": self.context.service.confirm_novel,
                    "pause": self.context.service.pause_novel,
                    "resume": self.context.service.resume_novel,
                    "recover": self.context.service.recover_known_issue,
                }[action]
                self._send_json(method(project_id))
                return
            match = re.fullmatch(r"/api/novels/([^/]+)/runs", path)
            if match:
                project_id = match.group(1)
                count = max(1, min(int(payload.get("count", 1)), 10))
                results = [
                    self.context.service.generate_next_chapter(project_id) for _ in range(count)
                ]
                self._send_json({"items": results})
                return
            match = re.fullmatch(r"/api/novels/([^/]+)/characters/rebuild", path)
            if match:
                self._send_json(
                    self.context.service.rebuild_character_archives(match.group(1))
                )
                return
            match = re.fullmatch(r"/api/novels/([^/]+)/brief-regenerate", path)
            if match:
                change = BriefRegenerateRequest.model_validate(payload)
                self._send_json(
                    self.context.service.regenerate_draft_brief(match.group(1), change)
                )
                return
            match = re.fullmatch(r"/api/novels/([^/]+)/brief-restore", path)
            if match:
                self._send_json(
                    self.context.service.restore_draft_brief(
                        match.group(1), int(payload.get("version", 0))
                    )
                )
                return
            match = re.fullmatch(r"/api/novels/([^/]+)/semantic-search", path)
            if match:
                self._send_json(
                    {
                        "items": self.context.service.semantic_search(
                            match.group(1),
                            str(payload.get("query", "")),
                            int(payload.get("limit", 6)),
                        )
                    }
                )
                return
            self._send_json({"error": "NOT_FOUND", "message": "接口不存在"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._handle_error(exc)

    def do_PUT(self) -> None:
        try:
            path = self.path.split("?", 1)[0]
            payload = self._read_json()
            match = re.fullmatch(r"/api/novels/([^/]+)/brief", path)
            if match:
                brief = CreativeBrief.model_validate(payload)
                self._send_json(
                    self.context.service.update_draft_brief(match.group(1), brief)
                )
                return
            if path != "/api/settings":
                self._send_json({"error": "NOT_FOUND", "message": "接口不存在"}, HTTPStatus.NOT_FOUND)
                return
            allowed = {"daily_chapters", "reserve_target", "debug_mode", "budget_limit"}
            for key, value in payload.items():
                if key not in allowed:
                    raise ValueError(f"设置项不可写：{key}")
                self.context.repository.save_setting(key, value)
            self._send_json({"preferences": self.context.repository.list_settings()})
        except Exception as exc:
            self._handle_error(exc)

    def do_DELETE(self) -> None:
        try:
            path = self.path.split("?", 1)[0]
            match = re.fullmatch(r"/api/novels/([^/]+)", path)
            if not match:
                self._send_json(
                    {"error": "NOT_FOUND", "message": "接口不存在"},
                    HTTPStatus.NOT_FOUND,
                )
                return
            self.context.service.delete_draft_novel(match.group(1))
            self._send_json({"deleted": True, "project_id": match.group(1)})
        except Exception as exc:
            self._handle_error(exc)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("请求体过大")
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(_jsonable(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, events: list[Any]) -> None:
        lines: list[str] = []
        for event in events:
            lines.append(f"id: {event.id}")
            lines.append("event: workflow")
            lines.append(
                "data: "
                + json.dumps(_jsonable(event), ensure_ascii=False, separators=(",", ":"))
            )
            lines.append("")
        body = ("\n".join(lines) + "\n").encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self._cors_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"/", ""} else path.lstrip("/")
        target = (self.context.web_root / relative).resolve()
        if self.context.web_root.resolve() not in target.parents and target != self.context.web_root.resolve():
            self._send_json({"error": "INVALID_PATH"}, HTTPStatus.BAD_REQUEST)
            return
        if not target.is_file():
            target = self.context.web_root / "index.html"
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,OPTIONS")

    def _handle_error(self, exc: Exception) -> None:
        status = HTTPStatus.INTERNAL_SERVER_ERROR
        error = type(exc).__name__
        message = str(exc)
        details: Any = None
        if isinstance(exc, ValidationError):
            status = HTTPStatus.UNPROCESSABLE_ENTITY
            error = "VALIDATION_ERROR"
            details = exc.errors(include_url=False)
        elif isinstance(exc, RequirementConflict):
            status = HTTPStatus.CONFLICT
            error = "REQUIREMENT_CONFLICT"
            details = exc.conflicts
        elif isinstance(exc, RepositoryError):
            status = HTTPStatus.NOT_FOUND
            error = "NOT_FOUND"
        elif isinstance(exc, WorkflowBlocked):
            status = HTTPStatus.CONFLICT
            error = "WORKFLOW_BLOCKED"
        elif isinstance(exc, (ValueError, json.JSONDecodeError)):
            status = HTTPStatus.BAD_REQUEST
            error = "BAD_REQUEST"
        self._send_json({"error": error, "message": message, "details": details}, status)


def serve(context: AppContext) -> None:
    handler = type("ConfiguredNovelAgentHandler", (NovelAgentHandler,), {"context": context})
    server = ThreadingHTTPServer((context.settings.host, context.settings.port), handler)
    print(f"Novel Agent 已启动：http://{context.settings.host}:{context.settings.port}")
    print(f"运行模式：{context.settings.mode}；模型适配器：{context.service.gateway.name}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止 Novel Agent…")
    finally:
        server.server_close()
