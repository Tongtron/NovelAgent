from pathlib import Path

from novel_agent.api import serve
from novel_agent.bootstrap import create_context


if __name__ == "__main__":
    serve(create_context(Path(__file__).resolve().parents[2]))

