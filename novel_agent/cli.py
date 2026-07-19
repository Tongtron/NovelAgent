from __future__ import annotations

import argparse
import json
from pathlib import Path

from novel_agent.api import serve
from novel_agent.bootstrap import create_context
from novel_agent.models import NovelCreateRequest
from novel_agent.scheduler import LocalScheduler


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novel-agent", description="个人自主小说作者智能体")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="启动控制台和 API")
    demo = sub.add_parser("demo", help="创建并运行一个离线示例")
    demo.add_argument("--chapters", type=int, default=1)
    generate = sub.add_parser("generate", help="为现有项目生成下一章")
    generate.add_argument("project_id")
    tick = sub.add_parser("tick", help="执行一次本地储备补充调度")
    tick.add_argument("--limit", type=int, default=1)
    sub.add_parser("list", help="列出项目")
    return parser


def main() -> None:
    args = _parser().parse_args()
    context = create_context(Path.cwd())
    if args.command == "serve":
        serve(context)
    elif args.command == "demo":
        project = context.service.create_novel(
            NovelCreateRequest(
                genre="末世",
                experiences=["爽文", "热血"],
                elements=["囤货", "经营建设"],
                protagonist_tags=["重生", "普通人成长"],
                idea="仓储管理员利用有限的职业知识建立一座可持续社区，没有无限空间。",
                exclude=["过度血腥"],
                require_plan_confirmation=False,
                reserve_target=max(1, args.chapters),
            )
        )
        results = [context.service.generate_next_chapter(project.id) for _ in range(args.chapters)]
        print(json.dumps({"project": project, "chapters": results}, default=lambda o: o.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "generate":
        result = context.service.generate_next_chapter(args.project_id)
        print(result.model_dump_json(indent=2))
    elif args.command == "tick":
        print(json.dumps(LocalScheduler(context.service).tick(args.limit), ensure_ascii=False, indent=2))
    elif args.command == "list":
        print(json.dumps([p.model_dump(mode="json") for p in context.repository.list_projects()], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

