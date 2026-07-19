from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from novel_agent.models import (
    ChapterPlan,
    ChapterRecord,
    ChapterStatus,
    CharacterRecord,
    CharacterMemoryUpdate,
    CreativeBrief,
    NovelCreateRequest,
    ProjectRecord,
    ProjectStatus,
    ReviewReport,
    RunEvent,
    WorldFactRecord,
    new_id,
    utc_now,
)


SCHEMA_VERSION = 3


class RepositoryError(RuntimeError):
    pass


class SQLiteRepository:
    """Authoritative local store with explicit transactions and immutable versions."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def migrate(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    brief_json TEXT NOT NULL,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    current_chapter INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS brief_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    brief_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    feedback TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, version)
                );

                CREATE TABLE IF NOT EXISTS chapters (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    number INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_json TEXT,
                    content TEXT,
                    summary TEXT,
                    version INTEGER NOT NULL DEFAULT 0,
                    locked INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, number)
                );

                CREATE TABLE IF NOT EXISTS chapter_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(chapter_id, version)
                );

                CREATE TABLE IF NOT EXISTS characters (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    immutable_facts_json TEXT NOT NULL,
                    current_state_json TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(project_id, name)
                );

                CREATE TABLE IF NOT EXISTS world_facts (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    category TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    evidence_chapter INTEGER,
                    version INTEGER NOT NULL DEFAULT 1,
                    locked INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS timeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    chapter_number INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, chapter_number, event)
                );

                CREATE TABLE IF NOT EXISTS foreshadows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    introduced_chapter INTEGER NOT NULL,
                    resolved_chapter INTEGER,
                    UNIQUE(project_id, label)
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    chapter_id TEXT,
                    status TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    project_id TEXT NOT NULL,
                    chapter_id TEXT,
                    node TEXT NOT NULL,
                    message TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    level TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS review_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
                    run_id TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    chapter_id TEXT,
                    query TEXT NOT NULL,
                    title TEXT,
                    url TEXT,
                    reliability REAL NOT NULL DEFAULT 0,
                    excerpt TEXT,
                    domain TEXT,
                    content TEXT,
                    verified INTEGER NOT NULL DEFAULT 0,
                    verification_notes TEXT,
                    published_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS semantic_documents (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    chapter_id TEXT,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    embedding_json TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, source_type, source_id)
                );

                CREATE INDEX IF NOT EXISTS idx_semantic_documents_project
                ON semantic_documents(project_id, source_type);

                CREATE TABLE IF NOT EXISTS publish_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    chapter_id TEXT NOT NULL REFERENCES chapters(id),
                    provider TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    external_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(research_sources)").fetchall()
            }
            additions = {
                "domain": "TEXT",
                "content": "TEXT",
                "verified": "INTEGER NOT NULL DEFAULT 0",
                "verification_notes": "TEXT",
                "published_at": "TEXT",
            }
            for name, definition in additions.items():
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE research_sources ADD COLUMN {name} {definition}"
                    )
            conn.execute(
                """INSERT OR IGNORE INTO brief_versions
                (project_id,version,brief_json,source,feedback,created_at)
                SELECT id,1,brief_json,'migration',NULL,created_at FROM projects"""
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_now()),
            )

    @staticmethod
    def _dump(value: Any) -> str:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=lambda item: item.model_dump(mode="json")
            if hasattr(item, "model_dump")
            else str(item),
        )

    def create_project(
        self,
        project: ProjectRecord,
        characters: list[CharacterRecord],
        world_facts: list[WorldFactRecord],
    ) -> ProjectRecord:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO projects
                (id,title,status,request_json,brief_json,confirmed,current_chapter,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    project.id,
                    project.title,
                    project.status.value,
                    self._dump(project.request),
                    self._dump(project.brief),
                    int(project.confirmed),
                    project.current_chapter,
                    project.created_at,
                    project.updated_at,
                ),
            )
            conn.execute(
                """INSERT INTO brief_versions
                (project_id,version,brief_json,source,feedback,created_at)
                VALUES (?,1,?,'initial',NULL,?)""",
                (project.id, self._dump(project.brief), project.created_at),
            )
            for character in characters:
                conn.execute(
                    """INSERT INTO characters
                    (id,project_id,name,role,immutable_facts_json,current_state_json,version)
                    VALUES (?,?,?,?,?,?,?)""",
                    (
                        character.id,
                        character.project_id,
                        character.name,
                        character.role,
                        self._dump(character.immutable_facts),
                        self._dump(character.current_state),
                        character.version,
                    ),
                )
            for fact in world_facts:
                conn.execute(
                    """INSERT INTO world_facts
                    (id,project_id,category,statement,evidence_chapter,version,locked)
                    VALUES (?,?,?,?,?,?,?)""",
                    (
                        fact.id,
                        fact.project_id,
                        fact.category,
                        fact.statement,
                        fact.evidence_chapter,
                        fact.version,
                        int(fact.locked),
                    ),
                )
        return project

    def list_projects(self) -> list[ProjectRecord]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
        return [self._project(row) for row in rows]

    def get_project(self, project_id: str) -> ProjectRecord:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise RepositoryError(f"找不到小说项目：{project_id}")
        return self._project(row)

    def update_project_status(self, project_id: str, status: ProjectStatus) -> ProjectRecord:
        with self.transaction() as conn:
            updated = conn.execute(
                "UPDATE projects SET status=?, updated_at=? WHERE id=?",
                (status.value, utc_now(), project_id),
            ).rowcount
            if not updated:
                raise RepositoryError(f"找不到小说项目：{project_id}")
        return self.get_project(project_id)

    def confirm_project(self, project_id: str) -> ProjectRecord:
        with self.transaction() as conn:
            updated = conn.execute(
                "UPDATE projects SET confirmed=1,status=?,updated_at=? WHERE id=?",
                (ProjectStatus.ACTIVE.value, utc_now(), project_id),
            ).rowcount
            if not updated:
                raise RepositoryError(f"找不到小说项目：{project_id}")
        return self.get_project(project_id)

    def save_draft_brief(
        self,
        project_id: str,
        brief: CreativeBrief,
        *,
        source: str,
        feedback: str = "",
    ) -> ProjectRecord:
        now = utc_now()
        with self.transaction() as conn:
            project = conn.execute(
                "SELECT status,confirmed,request_json FROM projects WHERE id=?",
                (project_id,),
            ).fetchone()
            if project is None:
                raise RepositoryError(f"找不到小说项目：{project_id}")
            if project["status"] != ProjectStatus.DRAFT.value or bool(project["confirmed"]):
                raise RepositoryError("创作方案确认后不能直接修改，请创建正式设定变更流程")
            chapter_count = conn.execute(
                "SELECT COUNT(*) AS count FROM chapters WHERE project_id=?", (project_id,)
            ).fetchone()["count"]
            if chapter_count:
                raise RepositoryError("项目已有章节，不能按草稿方案修改")
            request = NovelCreateRequest.model_validate_json(project["request_json"])
            next_version = int(
                conn.execute(
                    "SELECT COALESCE(MAX(version),0)+1 AS value FROM brief_versions WHERE project_id=?",
                    (project_id,),
                ).fetchone()["value"]
            )
            conn.execute(
                """UPDATE projects SET title=?,brief_json=?,updated_at=? WHERE id=?""",
                (brief.selected_title, self._dump(brief), now, project_id),
            )
            conn.execute(
                """INSERT INTO brief_versions
                (project_id,version,brief_json,source,feedback,created_at)
                VALUES (?,?,?,?,?,?)""",
                (
                    project_id,
                    next_version,
                    self._dump(brief),
                    source[:40],
                    feedback[:2000] or None,
                    now,
                ),
            )
            character = conn.execute(
                """SELECT id,current_state_json,version FROM characters
                WHERE project_id=? AND role='protagonist' ORDER BY id LIMIT 1""",
                (project_id,),
            ).fetchone()
            if character is None:
                raise RepositoryError("找不到主角人物记录")
            state = json.loads(character["current_state_json"])
            state["goal"] = brief.main_conflict
            protagonist_profile = next(
                (
                    profile
                    for profile in brief.character_profiles
                    if profile.name == brief.protagonist_name
                ),
                None,
            )
            if protagonist_profile:
                state["relationships"] = protagonist_profile.relationships
            state["importance"] = "core"
            immutable = [
                f"主角姓名为{brief.protagonist_name}",
                *(
                    [f"人物背景：{brief.protagonist_profile}"]
                    if brief.protagonist_profile
                    else []
                ),
                f"故事起点属于{request.genre}",
                "没有未经故事建立的无限资源或全知能力",
            ]
            if protagonist_profile and protagonist_profile.personality:
                immutable.insert(
                    -2,
                    f"性格特征：{'、'.join(protagonist_profile.personality)}",
                )
            conn.execute(
                """UPDATE characters SET name=?,immutable_facts_json=?,current_state_json=?,
                version=? WHERE id=?""",
                (
                    brief.protagonist_name,
                    self._dump(immutable),
                    self._dump(state),
                    int(character["version"]) + 1,
                    character["id"],
                ),
            )
            conn.execute(
                "DELETE FROM characters WHERE project_id=? AND role<>'protagonist'",
                (project_id,),
            )
            for profile in brief.character_profiles:
                if profile.name == brief.protagonist_name:
                    continue
                profile_facts = [
                    *([f"人物背景：{profile.profile}"] if profile.profile else []),
                    *(
                        [f"性格特征：{'、'.join(profile.personality)}"]
                        if profile.personality
                        else []
                    ),
                ]
                profile_state = {
                    "chapter": 0,
                    "importance": profile.importance,
                    "goal": profile.core_goal,
                    "knowledge": [],
                    "relationships": profile.relationships,
                }
                conn.execute(
                    """INSERT INTO characters
                    (id,project_id,name,role,immutable_facts_json,current_state_json,version)
                    VALUES (?,?,?,?,?,?,1)""",
                    (
                        new_id("character"),
                        project_id,
                        profile.name,
                        "supporting" if profile.role == "protagonist" else profile.role,
                        self._dump(profile_facts),
                        self._dump(profile_state),
                    ),
                )
            conn.execute("DELETE FROM world_facts WHERE project_id=?", (project_id,))
            for statement in brief.world_rules:
                conn.execute(
                    """INSERT INTO world_facts
                    (id,project_id,category,statement,evidence_chapter,version,locked)
                    VALUES (?,?,?,?,NULL,1,1)""",
                    (new_id("fact"), project_id, "hard_rule", statement),
                )
        return self.get_project(project_id)

    def list_brief_versions(self, project_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT version,brief_json,source,feedback,created_at FROM brief_versions
                WHERE project_id=? ORDER BY version DESC""",
                (project_id,),
            ).fetchall()
        return [
            {
                "version": row["version"],
                "brief": CreativeBrief.model_validate_json(row["brief_json"]),
                "source": row["source"],
                "feedback": row["feedback"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_brief_version(self, project_id: str, version: int) -> CreativeBrief:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT brief_json FROM brief_versions WHERE project_id=? AND version=?",
                (project_id, version),
            ).fetchone()
        if row is None:
            raise RepositoryError(f"找不到方案版本：v{version}")
        return CreativeBrief.model_validate_json(row["brief_json"])

    def delete_draft_project(self, project_id: str) -> None:
        with self.transaction() as conn:
            project = conn.execute(
                "SELECT status,confirmed FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if project is None:
                raise RepositoryError(f"找不到小说项目：{project_id}")
            if project["status"] != ProjectStatus.DRAFT.value or bool(project["confirmed"]):
                raise RepositoryError("只能删除尚未确认的草稿项目")
            conn.execute("DELETE FROM projects WHERE id=?", (project_id,))

    def create_chapter(self, chapter: ChapterRecord) -> ChapterRecord:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO chapters
                (id,project_id,number,title,status,plan_json,content,summary,version,locked,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    chapter.id,
                    chapter.project_id,
                    chapter.number,
                    chapter.title,
                    chapter.status.value,
                    self._dump(chapter.plan) if chapter.plan else None,
                    chapter.content,
                    chapter.summary,
                    chapter.version,
                    int(chapter.locked),
                    chapter.created_at,
                    chapter.updated_at,
                ),
            )
        return chapter

    def get_chapter(self, chapter_id: str) -> ChapterRecord:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone()
        if row is None:
            raise RepositoryError(f"找不到章节：{chapter_id}")
        return self._chapter(row)

    def get_chapter_by_number(self, project_id: str, number: int) -> ChapterRecord | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM chapters WHERE project_id=? AND number=?",
                (project_id, number),
            ).fetchone()
        return self._chapter(row) if row else None

    def list_chapters(self, project_id: str) -> list[ChapterRecord]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM chapters WHERE project_id=? ORDER BY number",
                (project_id,),
            ).fetchall()
        return [self._chapter(row) for row in rows]

    def update_chapter(
        self,
        chapter_id: str,
        *,
        status: ChapterStatus | None = None,
        plan: ChapterPlan | None = None,
        title: str | None = None,
    ) -> ChapterRecord:
        values: list[Any] = []
        fields: list[str] = []
        if status is not None:
            fields.append("status=?")
            values.append(status.value)
        if plan is not None:
            fields.append("plan_json=?")
            values.append(self._dump(plan))
        if title is not None:
            fields.append("title=?")
            values.append(title)
        fields.append("updated_at=?")
        values.append(utc_now())
        values.append(chapter_id)
        with self.transaction() as conn:
            updated = conn.execute(
                f"UPDATE chapters SET {','.join(fields)} WHERE id=?", values
            ).rowcount
            if not updated:
                raise RepositoryError(f"找不到章节：{chapter_id}")
        return self.get_chapter(chapter_id)

    def save_draft_version(self, chapter_id: str, content: str, metadata: dict[str, Any]) -> int:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 AS next_version FROM chapter_versions WHERE chapter_id=?",
                (chapter_id,),
            ).fetchone()
            version = int(row["next_version"])
            conn.execute(
                """INSERT INTO chapter_versions
                (chapter_id,version,kind,content,metadata_json,created_at) VALUES (?,?,?,?,?,?)""",
                (chapter_id, version, "DRAFT", content, self._dump(metadata), utc_now()),
            )
            conn.execute(
                "UPDATE chapters SET content=?,version=?,updated_at=? WHERE id=?",
                (content, version, utc_now(), chapter_id),
            )
        return version

    def commit_ready_chapter(
        self,
        chapter_id: str,
        run_id: str,
        content: str,
        summary: str,
        review: ReviewReport,
        timeline_event: str,
        character_updates: list[CharacterMemoryUpdate],
    ) -> ChapterRecord:
        """Atomically promotes content and official memory only after review passes."""
        with self.transaction() as conn:
            chapter = conn.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone()
            if chapter is None:
                raise RepositoryError(f"找不到章节：{chapter_id}")
            if chapter["status"] == ChapterStatus.READY.value:
                return self._chapter(chapter)
            row = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 AS next_version FROM chapter_versions WHERE chapter_id=?",
                (chapter_id,),
            ).fetchone()
            version = int(row["next_version"])
            now = utc_now()
            conn.execute(
                """INSERT INTO chapter_versions
                (chapter_id,version,kind,content,metadata_json,created_at) VALUES (?,?,?,?,?,?)""",
                (
                    chapter_id,
                    version,
                    "FINAL",
                    content,
                    self._dump({"review": review, "run_id": run_id}),
                    now,
                ),
            )
            conn.execute(
                """UPDATE chapters SET status=?,content=?,summary=?,version=?,updated_at=? WHERE id=?""",
                (ChapterStatus.READY.value, content, summary, version, now, chapter_id),
            )
            conn.execute(
                """UPDATE projects SET current_chapter=MAX(current_chapter,?),updated_at=? WHERE id=?""",
                (chapter["number"], now, chapter["project_id"]),
            )
            conn.execute(
                """INSERT OR IGNORE INTO timeline_events
                (project_id,chapter_number,event,created_at) VALUES (?,?,?,?)""",
                (chapter["project_id"], chapter["number"], timeline_event, now),
            )
            self._apply_character_updates(
                conn,
                chapter["project_id"],
                chapter["number"],
                character_updates,
            )
            conn.execute(
                "INSERT INTO review_reports(chapter_id,run_id,report_json,created_at) VALUES (?,?,?,?)",
                (chapter_id, run_id, self._dump(review), now),
            )
        return self.get_chapter(chapter_id)

    def upsert_character_memories(
        self,
        project_id: str,
        chapter_number: int,
        updates: list[CharacterMemoryUpdate],
    ) -> list[CharacterRecord]:
        with self.transaction() as conn:
            self._apply_character_updates(conn, project_id, chapter_number, updates)
        return self.list_characters(project_id)

    def _apply_character_updates(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        chapter_number: int,
        updates: list[CharacterMemoryUpdate],
    ) -> None:
        for character_update in updates:
            row = conn.execute(
                """SELECT id,role,immutable_facts_json,current_state_json,version
                FROM characters WHERE project_id=? AND name=?""",
                (project_id, character_update.name),
            ).fetchone()
            if row:
                state = json.loads(row["current_state_json"])
                facts = json.loads(row["immutable_facts_json"])
                if character_update.profile:
                    facts.append(f"人物背景：{character_update.profile}")
                facts.extend(character_update.immutable_facts)
                facts = list(dict.fromkeys(fact for fact in facts if str(fact).strip()))
                state["last_seen_chapter"] = max(
                    int(state.get("last_seen_chapter") or 0), chapter_number
                )
                state["importance"] = (
                    "core"
                    if row["role"] == "protagonist"
                    else character_update.importance
                )
                if character_update.current_goal:
                    state["current_goal"] = character_update.current_goal
                if character_update.last_result:
                    state["last_result"] = character_update.last_result
                if character_update.location:
                    state["location"] = character_update.location
                knowledge = list(state.get("knowledge") or [])
                state["knowledge"] = list(
                    dict.fromkeys([*knowledge, *character_update.knowledge_gained])
                )
                relationships = dict(state.get("relationships") or {})
                relationships.update(character_update.relationship_changes)
                state["relationships"] = relationships
                role = (
                    "protagonist"
                    if row["role"] == "protagonist"
                    else character_update.role or row["role"]
                )
                conn.execute(
                    """UPDATE characters SET role=?,immutable_facts_json=?,
                    current_state_json=?,version=? WHERE id=?""",
                    (
                        role,
                        self._dump(facts),
                        self._dump(state),
                        int(row["version"]) + 1,
                        row["id"],
                    ),
                )
                continue
            facts = [
                *(
                    [f"人物背景：{character_update.profile}"]
                    if character_update.profile
                    else []
                ),
                *character_update.immutable_facts,
            ]
            state = {
                "chapter": chapter_number,
                "importance": character_update.importance,
                "last_seen_chapter": chapter_number,
                "goal": character_update.current_goal,
                "current_goal": character_update.current_goal,
                "last_result": character_update.last_result,
                "location": character_update.location,
                "knowledge": character_update.knowledge_gained,
                "relationships": character_update.relationship_changes,
            }
            conn.execute(
                """INSERT INTO characters
                (id,project_id,name,role,immutable_facts_json,current_state_json,version)
                VALUES (?,?,?,?,?,?,1)""",
                (
                    new_id("character"),
                    project_id,
                    character_update.name,
                    character_update.role or "supporting",
                    self._dump(list(dict.fromkeys(facts))),
                    self._dump(state),
                ),
            )

    def list_characters(self, project_id: str) -> list[CharacterRecord]:
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM characters WHERE project_id=?
                ORDER BY CASE WHEN role='protagonist' THEN 0 ELSE 1 END,role,name""",
                (project_id,),
            ).fetchall()
        return [
            CharacterRecord(
                id=row["id"],
                project_id=row["project_id"],
                name=row["name"],
                role=row["role"],
                immutable_facts=json.loads(row["immutable_facts_json"]),
                current_state=json.loads(row["current_state_json"]),
                version=row["version"],
            )
            for row in rows
        ]

    def prune_character_archives(
        self,
        project_id: str,
        keep_names: set[str],
    ) -> int:
        """Remove non-protagonist archives not confirmed as important."""
        with self.transaction() as conn:
            if keep_names:
                placeholders = ",".join("?" for _ in keep_names)
                params: list[Any] = [project_id, *sorted(keep_names)]
                return conn.execute(
                    f"""DELETE FROM characters WHERE project_id=?
                    AND role<>'protagonist' AND name NOT IN ({placeholders})""",
                    params,
                ).rowcount
            return conn.execute(
                "DELETE FROM characters WHERE project_id=? AND role<>'protagonist'",
                (project_id,),
            ).rowcount

    def list_world_facts(self, project_id: str) -> list[WorldFactRecord]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM world_facts WHERE project_id=? ORDER BY category,id", (project_id,)
            ).fetchall()
        return [
            WorldFactRecord(
                id=row["id"],
                project_id=row["project_id"],
                category=row["category"],
                statement=row["statement"],
                evidence_chapter=row["evidence_chapter"],
                version=row["version"],
                locked=bool(row["locked"]),
            )
            for row in rows
        ]

    def create_run(self, run_id: str, project_id: str, chapter_id: str) -> None:
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO runs
                (id,project_id,chapter_id,status,checkpoint_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?)""",
                (run_id, project_id, chapter_id, "RUNNING", "{}", now, now),
            )

    def update_run(
        self,
        run_id: str,
        status: str,
        checkpoint: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """UPDATE runs SET status=?,checkpoint_json=?,error_code=?,updated_at=? WHERE id=?""",
                (status, self._dump(checkpoint or {}), error_code, utc_now(), run_id),
            )

    def get_latest_run_diagnostic(self, project_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                """SELECT id,chapter_id,status,checkpoint_json,error_code,updated_at
                FROM runs WHERE project_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["checkpoint"] = json.loads(result.pop("checkpoint_json") or "{}")
        return result

    def recover_protagonist_name_issue(
        self,
        project_id: str,
        brief: CreativeBrief,
        name: str,
        profile: str,
        chapter_id: str,
    ) -> ProjectRecord:
        """Atomically repair legacy character data without discarding draft history."""
        now = utc_now()
        with self.transaction() as conn:
            character = conn.execute(
                """SELECT id,immutable_facts_json,version FROM characters
                WHERE project_id=? AND role='protagonist' ORDER BY id LIMIT 1""",
                (project_id,),
            ).fetchone()
            if character is None:
                raise RepositoryError("找不到主角人物记录")
            facts = json.loads(character["immutable_facts_json"])
            facts = [fact for fact in facts if not str(fact).startswith("主角姓名为")]
            facts.insert(0, f"主角姓名为{name}")
            profile_fact = f"人物背景：{profile}" if profile else ""
            if profile_fact and profile_fact not in facts:
                facts.insert(1, profile_fact)
            conn.execute(
                """UPDATE characters SET name=?,immutable_facts_json=?,version=? WHERE id=?""",
                (name, self._dump(facts), int(character["version"]) + 1, character["id"]),
            )
            conn.execute(
                """UPDATE projects SET brief_json=?,status=?,updated_at=? WHERE id=?""",
                (self._dump(brief), ProjectStatus.ACTIVE.value, now, project_id),
            )
            updated = conn.execute(
                """UPDATE chapters SET status=?,updated_at=?
                WHERE id=? AND project_id=? AND status=?""",
                (
                    ChapterStatus.PLANNED.value,
                    now,
                    chapter_id,
                    project_id,
                    ChapterStatus.FAILED.value,
                ),
            ).rowcount
            if not updated:
                raise RepositoryError("失败章节状态已变化，未执行恢复")
        return self.get_project(project_id)

    def add_event(self, event: RunEvent) -> RunEvent:
        with self.transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO run_events
                (run_id,project_id,chapter_id,node,message,progress,level,payload_json,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    event.run_id,
                    event.project_id,
                    event.chapter_id,
                    event.node,
                    event.message,
                    event.progress,
                    event.level,
                    self._dump(event.payload),
                    event.created_at,
                ),
            )
            event.id = int(cursor.lastrowid)
        return event

    def list_events(self, run_id: str, after_id: int = 0) -> list[RunEvent]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM run_events WHERE run_id=? AND id>? ORDER BY id",
                (run_id, after_id),
            ).fetchall()
        return [
            RunEvent(
                id=row["id"],
                run_id=row["run_id"],
                project_id=row["project_id"],
                chapter_id=row["chapter_id"],
                node=row["node"],
                message=row["message"],
                progress=row["progress"],
                level=row["level"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_timeline(self, project_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM timeline_events WHERE project_id=? ORDER BY chapter_number,id",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_research_sources(
        self,
        project_id: str,
        chapter_id: str,
        sources: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        saved: list[dict[str, Any]] = []
        with self.transaction() as conn:
            for source in sources:
                cursor = conn.execute(
                    """INSERT INTO research_sources
                    (project_id,chapter_id,query,title,url,reliability,excerpt,domain,content,
                     verified,verification_notes,published_at,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        project_id,
                        chapter_id,
                        str(source.get("query", ""))[:1000],
                        str(source.get("title", ""))[:500] or None,
                        str(source.get("url", ""))[:2000] or None,
                        float(source.get("reliability", 0.0)),
                        str(source.get("excerpt") or source.get("fact") or "")[:4000],
                        str(source.get("domain", ""))[:300] or None,
                        str(source.get("content") or source.get("excerpt") or "")[:12000],
                        int(bool(source.get("verified"))),
                        str(source.get("verification_notes", ""))[:1000] or None,
                        str(source.get("published_at", ""))[:100] or None,
                        utc_now(),
                    ),
                )
                row = dict(source)
                row["id"] = int(cursor.lastrowid)
                saved.append(row)
        return saved

    def list_research_sources(self, project_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT id,project_id,chapter_id,query,title,url,reliability,excerpt,
                domain,verified,verification_notes,published_at,created_at
                FROM research_sources WHERE project_id=? ORDER BY id DESC""",
                (project_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["verified"] = bool(item["verified"])
            result.append(item)
        return result

    def list_pending_semantic_inputs(self, project_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            chapters = conn.execute(
                """SELECT c.id,c.id AS chapter_id,c.summary AS content,c.number,c.title
                FROM chapters c
                WHERE c.project_id=? AND c.status=? AND c.summary IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM semantic_documents s
                    WHERE s.project_id=c.project_id AND s.source_type='chapter_summary'
                    AND s.source_id=c.id
                ) ORDER BY c.number""",
                (project_id, ChapterStatus.READY.value),
            ).fetchall()
            sources = conn.execute(
                """SELECT r.id,r.chapter_id,COALESCE(r.content,r.excerpt,'') AS content,
                r.title,r.url,r.query
                FROM research_sources r
                WHERE r.project_id=? AND COALESCE(r.content,r.excerpt,'')!=''
                AND NOT EXISTS (
                    SELECT 1 FROM semantic_documents s
                    WHERE s.project_id=r.project_id AND s.source_type='research_source'
                    AND s.source_id=CAST(r.id AS TEXT)
                ) ORDER BY r.id""",
                (project_id,),
            ).fetchall()
        pending = [
            {
                "source_type": "chapter_summary",
                "source_id": row["id"],
                "chapter_id": row["chapter_id"],
                "content": row["content"],
                "metadata": {"number": row["number"], "title": row["title"]},
            }
            for row in chapters
        ]
        pending.extend(
            {
                "source_type": "research_source",
                "source_id": str(row["id"]),
                "chapter_id": row["chapter_id"],
                "content": row["content"],
                "metadata": {
                    "title": row["title"],
                    "url": row["url"],
                    "query": row["query"],
                },
            }
            for row in sources
        )
        return pending

    def upsert_semantic_documents(
        self,
        project_id: str,
        documents: list[dict[str, Any]],
        vectors: list[list[float]],
        model: str,
    ) -> None:
        if len(documents) != len(vectors):
            raise RepositoryError("语义文档与向量数量不一致")
        now = utc_now()
        with self.transaction() as conn:
            for document, vector in zip(documents, vectors, strict=True):
                if not vector:
                    raise RepositoryError("不能保存空向量")
                conn.execute(
                    """INSERT INTO semantic_documents
                    (id,project_id,chapter_id,source_type,source_id,content,metadata_json,
                     embedding_json,embedding_model,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(project_id,source_type,source_id) DO UPDATE SET
                    chapter_id=excluded.chapter_id,content=excluded.content,
                    metadata_json=excluded.metadata_json,embedding_json=excluded.embedding_json,
                    embedding_model=excluded.embedding_model,updated_at=excluded.updated_at""",
                    (
                        new_id("semantic"),
                        project_id,
                        document.get("chapter_id"),
                        document["source_type"],
                        str(document["source_id"]),
                        str(document["content"]),
                        self._dump(document.get("metadata", {})),
                        self._dump([float(value) for value in vector]),
                        model,
                        now,
                        now,
                    ),
                )

    def semantic_search(
        self, project_id: str, query_vector: list[float], limit: int = 6
    ) -> list[dict[str, Any]]:
        if not query_vector:
            return []
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT id,chapter_id,source_type,source_id,content,metadata_json,
                embedding_json,embedding_model FROM semantic_documents WHERE project_id=?""",
                (project_id,),
            ).fetchall()
        query_norm = math.sqrt(sum(value * value for value in query_vector))
        if query_norm == 0:
            return []
        matches: list[dict[str, Any]] = []
        for row in rows:
            vector = [float(value) for value in json.loads(row["embedding_json"])]
            if len(vector) != len(query_vector):
                continue
            norm = math.sqrt(sum(value * value for value in vector))
            if norm == 0:
                continue
            score = sum(a * b for a, b in zip(query_vector, vector, strict=True)) / (
                query_norm * norm
            )
            matches.append(
                {
                    "id": row["id"],
                    "chapter_id": row["chapter_id"],
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "content": row["content"],
                    "metadata": json.loads(row["metadata_json"]),
                    "embedding_model": row["embedding_model"],
                    "score": round(score, 6),
                }
            )
        matches.sort(key=lambda item: item["score"], reverse=True)
        return matches[: max(1, min(int(limit), 20))]

    def semantic_document_count(self, project_id: str) -> int:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM semantic_documents WHERE project_id=?",
                (project_id,),
            ).fetchone()
        return int(row["count"])

    def reserve_count(self, project_id: str) -> int:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM chapters WHERE project_id=? AND status=?",
                (project_id, ChapterStatus.READY.value),
            ).fetchone()
        return int(row["count"])

    def save_setting(self, key: str, value: Any) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO settings(key,value_json,updated_at) VALUES (?,?,?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
                (key, self._dump(value), utc_now()),
            )

    def list_settings(self) -> dict[str, Any]:
        with self.connection() as conn:
            rows = conn.execute("SELECT key,value_json FROM settings ORDER BY key").fetchall()
        return {row["key"]: json.loads(row["value_json"]) for row in rows}

    def _project(self, row: sqlite3.Row) -> ProjectRecord:
        return ProjectRecord(
            id=row["id"],
            title=row["title"],
            status=ProjectStatus(row["status"]),
            request=NovelCreateRequest.model_validate_json(row["request_json"]),
            brief=CreativeBrief.model_validate_json(row["brief_json"]),
            confirmed=bool(row["confirmed"]),
            current_chapter=row["current_chapter"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _chapter(self, row: sqlite3.Row) -> ChapterRecord:
        return ChapterRecord(
            id=row["id"],
            project_id=row["project_id"],
            number=row["number"],
            title=row["title"],
            status=ChapterStatus(row["status"]),
            plan=ChapterPlan.model_validate_json(row["plan_json"]) if row["plan_json"] else None,
            content=row["content"],
            summary=row["summary"],
            version=row["version"],
            locked=bool(row["locked"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
