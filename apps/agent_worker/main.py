"""Manual agent worker entry point for development and process separation tests."""

from pathlib import Path
import sys

from novel_agent.bootstrap import create_context


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法：python apps/agent_worker/main.py <project_id>")
    result = create_context(Path(__file__).resolve().parents[2]).service.generate_next_chapter(sys.argv[1])
    print(result.model_dump_json(indent=2))

