from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence

from .config import Settings
from .database import Database, ensure_database
from .engine import scan_project
from .exporter import export_obsidian
from .hooks import ingest_stdin
from .integrations import CONTEXT_FIRST_SKILL, hook_template, install_integration
from .query import (
    build_context,
    context_data,
    format_impact,
    history_data,
    impact_data,
    search_nodes,
)
from .registry import (
    ProjectNotFoundError,
    add_project,
    list_projects,
    remove_project,
    resolve_project,
)
from .tasks import close_task, create_task, get_task, list_tasks, record_event
from .utils import json_dumps


def _database(args: argparse.Namespace) -> Database:
    settings = Settings.load(args.home)
    return ensure_database(settings)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _resolve(database: Database, selector: str | None) -> sqlite3.Row:
    return resolve_project(database, selector, cwd=Path.cwd())


def command_init(args: argparse.Namespace) -> int:
    database = _database(args)
    print(f"AgentNavi 已初始化：{database.settings.home}")
    print(f"图谱数据库：{database.settings.database_path}")
    print(f"Obsidian 默认导出目录：{database.settings.obsidian_vault}")
    return 0


def command_project_add(args: argparse.Namespace) -> int:
    database = _database(args)
    project = add_project(
        database,
        args.path,
        project_id=args.project_id,
        name=args.name,
        kind=args.kind,
    )
    print(f"已关联项目：{project['name']}（{project['id']}）")
    print(f"项目路径：{project['root']}")
    if args.scan:
        report = scan_project(database, project, full=True)
        _print_scan_report(report)
    return 0


def command_project_list(args: argparse.Namespace) -> int:
    database = _database(args)
    projects = list_projects(database)
    if args.json:
        _print_json([dict(project) for project in projects])
        return 0
    if not projects:
        print("尚未关联项目。使用：agentnavi project add <路径>")
        return 0
    for project in projects:
        last_scan = project["last_scan_at"] or "未索引"
        print(f"- {project['id']} · {project['name']} · {project['kind']} · {project['root']} · {last_scan}")
    return 0


def command_project_remove(args: argparse.Namespace) -> int:
    database = _database(args)
    project = remove_project(database, args.project)
    print(f"已移除项目索引：{project['name']}（不会删除原项目文件）")
    return 0


def _print_scan_report(report: Any) -> None:
    print(
        "索引完成："
        f"发现 {report.total_files} 个文件；"
        f"重扫 {report.changed_files} 个；"
        f"删除 {report.deleted_files} 个；"
        f"L1 {report.physical_nodes} 节点/{report.physical_edges} 边；"
        f"L2 {report.semantic_nodes} 节点/{report.semantic_edges} 边。"
    )


def command_scan(args: argparse.Namespace) -> int:
    database = _database(args)
    project = _resolve(database, args.project)
    report = scan_project(database, project, full=args.full)
    if args.json:
        _print_json(asdict(report))
    else:
        _print_scan_report(report)
    return 0


def command_query(args: argparse.Namespace) -> int:
    database = _database(args)
    project = _resolve(database, args.project)
    with database.connect() as connection:
        rows = search_nodes(
            connection,
            project_id=project["id"],
            query=args.text,
            limit=args.limit,
        )
    payload = [
        {
            "id": row["id"],
            "layer": row["layer"],
            "kind": row["kind"],
            "key": row["key"],
            "label": row["label"],
            "confidence": row["confidence"],
            "source": row["source"],
        }
        for row in rows
    ]
    if args.json:
        _print_json(payload)
    elif not payload:
        print("没有匹配节点。先运行 agentnavi scan，或换用更接近项目用语的关键词。")
    else:
        for item in payload:
            print(
                f"- L{item['layer']} {item['kind']} · {item['label']} · "
                f"{item['key']} · {item['confidence']:.2f} · {item['source']}"
            )
    return 0


def command_context(args: argparse.Namespace) -> int:
    database = _database(args)
    project = _resolve(database, args.project)
    if args.json:
        _print_json(context_data(database, project, args.text))
    else:
        print(build_context(database, project, args.text))
    return 0


def command_impact(args: argparse.Namespace) -> int:
    database = _database(args)
    project = _resolve(database, args.project)
    data = impact_data(database, project, args.selector)
    if args.json:
        _print_json(data)
    else:
        print(format_impact(data))
    return 0


def command_history(args: argparse.Namespace) -> int:
    database = _database(args)
    project = _resolve(database, args.project)
    rows = history_data(database, project, args.text or "", limit=args.limit)
    if args.json:
        _print_json(rows)
    elif not rows:
        print("没有匹配的任务历史。")
    else:
        for row in rows:
            summary = row["summary"].replace("\n", " ").strip()
            if len(summary) > 160:
                summary = summary[:157] + "..."
            suffix = f" · {summary}" if summary else ""
            print(f"- {row['created_at']} · {row['title']} [{row['status']}] · {row['id']}{suffix}")
    return 0


def command_task_start(args: argparse.Namespace) -> int:
    database = _database(args)
    project = _resolve(database, args.project)
    task = create_task(
        database,
        project_id=project["id"],
        title=args.title,
        prompt=args.prompt or args.title,
        agent=args.agent,
        external_session_id=args.session,
    )
    if args.json:
        _print_json(dict(task))
    else:
        print(f"任务已创建：{task['id']} · {task['title']}")
    return 0


def command_task_event(args: argparse.Namespace) -> int:
    database = _database(args)
    task = get_task(database, args.task_id)
    project = resolve_project(database, task["project_id"])
    data: Any = {}
    if args.data:
        data = json.loads(args.data)
    record_event(
        database,
        project=project,
        agent=args.agent or task["agent"],
        event_type=args.event_type,
        task_id=task["id"],
        tool_name=args.tool,
        paths=args.path or (),
        data=data,
    )
    print(f"已记录事件：{args.event_type}")
    return 0


def command_task_close(args: argparse.Namespace) -> int:
    database = _database(args)
    task = close_task(database, args.task_id, status=args.status, summary=args.summary or "")
    if args.json:
        _print_json(dict(task))
    else:
        print(f"任务已结束：{task['id']} [{task['status']}]")
    return 0


def command_task_list(args: argparse.Namespace) -> int:
    database = _database(args)
    project = _resolve(database, args.project)
    rows = list_tasks(database, project["id"], limit=args.limit)
    if args.json:
        _print_json([dict(row) for row in rows])
    else:
        for row in rows:
            print(f"- {row['created_at']} · {row['title']} [{row['status']}] · {row['id']}")
    return 0


def command_export_obsidian(args: argparse.Namespace) -> int:
    database = _database(args)
    report = export_obsidian(
        database,
        destination=args.destination,
        project_selector=args.project,
        clean=not args.no_clean,
    )
    if args.json:
        _print_json(
            {
                "destination": str(report.destination),
                "projects": report.projects,
                "concepts": report.concepts,
                "tasks": report.tasks,
                "files_written": report.files_written,
            }
        )
    else:
        print(
            f"Obsidian 视图已生成：{report.destination / 'AgentNavi'}\n"
            f"{report.projects} 个项目 / {report.concepts} 个概念 / "
            f"{report.tasks} 个任务 / {report.files_written} 个 Markdown 文件"
        )
    return 0


def command_hook_ingest(args: argparse.Namespace) -> int:
    database = _database(args)
    try:
        result = ingest_stdin(database, agent=args.agent)
        if result.stdout:
            print(result.stdout)
    except Exception as exc:  # noqa: BLE001 - Hook 必须 fail-open
        print(f"AgentNavi hook 记录失败：{exc}", file=sys.stderr)
        # Stop hook 输出合法 JSON，其他事件保持空输出；全部返回 0，不阻断 Agent。
        print("{}")
    return 0


def command_integration_show(args: argparse.Namespace) -> int:
    if args.agent == "skill":
        print(CONTEXT_FIRST_SKILL)
    else:
        _print_json(hook_template(args.agent))
    return 0


def command_integration_install(args: argparse.Namespace) -> int:
    agents = ["codex", "claude"] if args.agent == "all" else [args.agent]
    if len(agents) > 1 and args.hook_path:
        raise ValueError("安装全部集成时不能共用 --hook-path；请分别安装 Codex 与 Claude")
    for agent in agents:
        report = install_integration(
            agent,
            hook_path=args.hook_path,
            install_skill=not args.no_skill,
        )
        print(f"已安装 {agent} Hook：{report.hook_path}")
        if report.skill_path:
            print(f"已安装 {agent} Skill：{report.skill_path}")
        if report.backup_path:
            print(f"原配置备份：{report.backup_path}")
    print("请确认 agentnavi 命令在 Agent 进程的 PATH 中，并在工具内审查、信任新 Hook。")
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    database = _database(args)
    checks: list[tuple[str, bool, str]] = []
    checks.append(("数据目录", database.settings.home.exists(), str(database.settings.home)))
    checks.append(("SQLite 数据库", database.settings.database_path.exists(), str(database.settings.database_path)))
    checks.append(("Git", shutil.which("git") is not None, shutil.which("git") or "未找到"))
    checks.append(("CLI", shutil.which("agentnavi") is not None, shutil.which("agentnavi") or sys.executable))
    projects = list_projects(database)
    missing = [project["root"] for project in projects if not Path(project["root"]).exists()]
    checks.append(("项目注册表", not missing, f"{len(projects)} 个项目" + (f"；失效：{missing}" if missing else "")))
    if database.settings.semantic_command:
        executable = database.settings.semantic_command.split()[0]
        checks.append(("外部语义提供器", shutil.which(executable) is not None, database.settings.semantic_command))
    else:
        checks.append(("外部语义提供器", True, "未配置；当前使用内置保守语义推断"))

    success = True
    for name, passed, detail in checks:
        success &= passed
        print(f"{'通过' if passed else '失败'} · {name} · {detail}")
    return 0 if success else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentnavi",
        description="独立于项目仓库的三层 Agent 上下文导航引擎",
    )
    parser.add_argument(
        "--home",
        help="AgentNavi 数据目录；默认读取 AGENTNAVI_HOME 或 ~/.agentnavi",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="初始化外部工作区和 SQLite 数据库")
    init_parser.set_defaults(func=command_init)

    project_parser = subparsers.add_parser("project", help="管理项目注册表")
    project_sub = project_parser.add_subparsers(dest="project_command", required=True)
    project_add = project_sub.add_parser("add", help="关联一个项目，项目本身不会被写入文件")
    project_add.add_argument("path", nargs="?", default=".")
    project_add.add_argument("--id", dest="project_id")
    project_add.add_argument("--name")
    project_add.add_argument("--kind", choices=["software", "writing", "video", "generic"])
    project_add.add_argument("--no-scan", dest="scan", action="store_false")
    project_add.set_defaults(func=command_project_add, scan=True)
    project_list = project_sub.add_parser("list", help="列出已关联项目")
    project_list.add_argument("--json", action="store_true")
    project_list.set_defaults(func=command_project_list)
    project_remove = project_sub.add_parser("remove", help="移除派生索引，不删除项目")
    project_remove.add_argument("project")
    project_remove.set_defaults(func=command_project_remove)

    scan_parser = subparsers.add_parser("scan", help="增量更新 L1/L2 图谱")
    scan_parser.add_argument("project", nargs="?")
    scan_parser.add_argument("--full", action="store_true", help="强制全量重建物理层")
    scan_parser.add_argument("--json", action="store_true")
    scan_parser.set_defaults(func=command_scan)

    query_parser = subparsers.add_parser("query", help="搜索图谱节点")
    query_parser.add_argument("text")
    query_parser.add_argument("--project")
    query_parser.add_argument("--limit", type=int, default=20)
    query_parser.add_argument("--json", action="store_true")
    query_parser.set_defaults(func=command_query)

    context_parser = subparsers.add_parser("context", help="按“任务→概念→文件”生成紧凑上下文")
    context_parser.add_argument("text", nargs="?", default="")
    context_parser.add_argument("--project")
    context_parser.add_argument("--json", action="store_true")
    context_parser.set_defaults(func=command_context)

    impact_parser = subparsers.add_parser("impact", help="分析一个文件的物理与语义影响")
    impact_parser.add_argument("selector")
    impact_parser.add_argument("--project")
    impact_parser.add_argument("--json", action="store_true")
    impact_parser.set_defaults(func=command_impact)

    history_parser = subparsers.add_parser("history", help="查询相关任务与决策历史")
    history_parser.add_argument("text", nargs="?", default="")
    history_parser.add_argument("--project")
    history_parser.add_argument("--limit", type=int, default=20)
    history_parser.add_argument("--json", action="store_true")
    history_parser.set_defaults(func=command_history)

    task_parser = subparsers.add_parser("task", help="手动记录 L3 任务图")
    task_sub = task_parser.add_subparsers(dest="task_command", required=True)
    task_start = task_sub.add_parser("start")
    task_start.add_argument("title")
    task_start.add_argument("--prompt")
    task_start.add_argument("--project")
    task_start.add_argument("--agent", default="manual")
    task_start.add_argument("--session")
    task_start.add_argument("--json", action="store_true")
    task_start.set_defaults(func=command_task_start)
    task_event = task_sub.add_parser("event")
    task_event.add_argument("task_id")
    task_event.add_argument("event_type", choices=["read", "modify", "write", "test", "search", "command", "note"])
    task_event.add_argument("--path", action="append")
    task_event.add_argument("--tool")
    task_event.add_argument("--agent")
    task_event.add_argument("--data", help="JSON 对象或数组")
    task_event.set_defaults(func=command_task_event)
    task_close = task_sub.add_parser("close")
    task_close.add_argument("task_id")
    task_close.add_argument("--status", default="completed", choices=["completed", "failed", "cancelled", "interrupted"])
    task_close.add_argument("--summary")
    task_close.add_argument("--json", action="store_true")
    task_close.set_defaults(func=command_task_close)
    task_list = task_sub.add_parser("list")
    task_list.add_argument("--project")
    task_list.add_argument("--limit", type=int, default=20)
    task_list.add_argument("--json", action="store_true")
    task_list.set_defaults(func=command_task_list)

    export_parser = subparsers.add_parser("export", help="导出派生视图")
    export_sub = export_parser.add_subparsers(dest="export_command", required=True)
    export_obsidian_parser = export_sub.add_parser("obsidian", help="生成可删除、可重建的 Obsidian Vault 视图")
    export_obsidian_parser.add_argument("--destination")
    export_obsidian_parser.add_argument("--project")
    export_obsidian_parser.add_argument("--no-clean", action="store_true")
    export_obsidian_parser.add_argument("--json", action="store_true")
    export_obsidian_parser.set_defaults(func=command_export_obsidian)

    hook_parser = subparsers.add_parser("hook", help="接收 Agent 生命周期事件")
    hook_sub = hook_parser.add_subparsers(dest="hook_command", required=True)
    hook_ingest = hook_sub.add_parser("ingest")
    hook_ingest.add_argument("--agent", required=True, choices=["codex", "claude", "generic"])
    hook_ingest.set_defaults(func=command_hook_ingest)

    integration_parser = subparsers.add_parser("integration", help="安装或查看 Codex / Claude Code 全局集成")
    integration_sub = integration_parser.add_subparsers(dest="integration_command", required=True)
    integration_show = integration_sub.add_parser("show", help="输出 Hook 模板或上下文优先 Skill")
    integration_show.add_argument("agent", choices=["codex", "claude", "skill"])
    integration_show.set_defaults(func=command_integration_show)
    integration_install = integration_sub.add_parser("install", help="幂等合并用户级 Hook，并安装全局 Skill")
    integration_install.add_argument("agent", choices=["codex", "claude", "all"])
    integration_install.add_argument("--hook-path", help="自定义 Hook/settings JSON 路径；主要用于测试或特殊配置")
    integration_install.add_argument("--no-skill", action="store_true")
    integration_install.set_defaults(func=command_integration_install)

    doctor_parser = subparsers.add_parser("doctor", help="检查本机安装和项目注册表")
    doctor_parser.set_defaults(func=command_doctor)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ProjectNotFoundError, FileNotFoundError, LookupError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
