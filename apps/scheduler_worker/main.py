"""One-shot local scheduler. A durable Temporal worker can replace this boundary later."""

from pathlib import Path

from novel_agent.bootstrap import create_context
from novel_agent.scheduler import LocalScheduler


if __name__ == "__main__":
    print(LocalScheduler(create_context(Path(__file__).resolve().parents[2]).service).tick())

