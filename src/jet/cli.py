import argparse
import logging
import os
from pathlib import Path
import secrets
import sys
import httpx
import uvicorn

import jet
from jet.api.app import create_app
from jet.config import load_settings
from jet.db.store import connect, init_db, open_db
from jet.eval.runner import export_jobs, import_reference, report_eval, run_eval
from jet.llm.quota import usage_today


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for Jet."""
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(prog="jet", description="Jet: 本地优先求职决策工作台")
    parser.add_argument("--version", action="version", version=jet.__version__)

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # jet serve
    serve_parser = subparsers.add_parser("serve", help="启动 Jet 服务")
    serve_parser.add_argument("--data-dir", type=str, default=None, help="数据目录路径")

    # jet pair
    pair_parser = subparsers.add_parser("pair", help="生成插件配对码")
    pair_parser.add_argument("--data-dir", type=str, default=None, help="数据目录路径")

    # jet stats
    stats_parser = subparsers.add_parser("stats", help="查看统计数据")
    stats_parser.add_argument("--data-dir", type=str, default=None, help="数据目录路径")

    # jet eval
    eval_parser = subparsers.add_parser("eval", help="评测提示词与模型组合")
    eval_sub = eval_parser.add_subparsers(dest="eval_command")

    run_p = eval_sub.add_parser("run", help="运行评测")
    run_p.add_argument("--variants", type=str, default="A,B,C", help="要评测的组合 (A,B,C)")
    run_p.add_argument("--max-calls", type=int, default=None, help="单次评测最大调用次数")
    run_p.add_argument("--data-dir", type=str, default=None, help="数据目录路径")

    rep_p = eval_sub.add_parser("report", help="查看评测报告")
    rep_p.add_argument("run_id", nargs="?", default=None, help="评测运行 ID (默认最新)")
    rep_p.add_argument("--data-dir", type=str, default=None, help="数据目录路径")

    exp_p = eval_sub.add_parser("export-jobs", help="导出岗位供参考标注")
    exp_p.add_argument("--out", type=str, required=True, help="导出文件路径")
    exp_p.add_argument("--limit", type=int, default=100, help="最多导出岗位数 (默认 100)")
    exp_p.add_argument("--data-dir", type=str, default=None, help="数据目录路径")

    imp_p = eval_sub.add_parser("import-reference", help="导入模型参考标注")
    imp_p.add_argument("file", type=str, help="参考标注文件路径")
    imp_p.add_argument("--source", type=str, default=None, help="标注来源 (覆盖文件中的 source)")
    imp_p.add_argument("--data-dir", type=str, default=None, help="数据目录路径")

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 0

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "serve":
        data_dir_path = Path(args.data_dir) if args.data_dir else None
        settings = load_settings(data_dir=data_dir_path)

        # 已有 Jet 在同一端口运行时不再启动：否则会覆盖、并在退出时删掉它的 admin.secret
        try:
            httpx.get(f"http://127.0.0.1:{settings.port}/v1/health", timeout=1.0)
            print(f"端口 {settings.port} 上已有程序在运行（可能是另一个 jet serve），未启动")
            return 1
        except httpx.HTTPError:
            pass

        # Check migration / init database
        migration_info = init_db(settings.data_dir)
        if migration_info:
            print(
                f"已从结构版本 {migration_info.from_version} 迁移到 {migration_info.to_version}，"
                f"备份：{migration_info.backup_filename}"
            )
            if migration_info.cleared:
                for _, job_title, field_name, old_val in migration_info.cleared:
                    print(f"需要重新标注：{job_title} 的 {field_name}（原值：{old_val}）")

        # Generate admin secret and write to <data_dir>/admin.secret (created as 0600, never world-readable)
        admin_secret = secrets.token_urlsafe(32)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        secret_file = settings.data_dir / "admin.secret"
        secret_file.unlink(missing_ok=True)
        fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(admin_secret)

        # 今日判断额度：上限直接用配置值（应用启动时才写进数据库，这里读库会是改配置前的旧值）
        conn = open_db(settings.data_dir)
        used = usage_today(conn, "me", purpose="judge")["used"]
        limit = settings.daily_llm_limit
        conn.close()

        print(f"Jet listening on http://127.0.0.1:{settings.port}")
        print(f"数据目录: {settings.data_dir}")
        print(f"今日额度: {used}/{limit}")

        # 配置 logging.getLogger("jet") 输出到终端（INFO 级别，格式 %(asctime)s %(message)s，时间精确到秒）
        jet_logger = logging.getLogger("jet")
        jet_logger.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        if not any(isinstance(h, logging.StreamHandler) for h in jet_logger.handlers):
            handler = logging.StreamHandler()
            handler.setLevel(logging.INFO)
            handler.setFormatter(formatter)
            jet_logger.addHandler(handler)
        else:
            for h in jet_logger.handlers:
                if isinstance(h, logging.StreamHandler):
                    h.setLevel(logging.INFO)
                    h.setFormatter(formatter)

        app = create_app(settings, admin_secret=admin_secret)
        app.state.created_admin_secret_file = True
        try:
            uvicorn.run(app, host="127.0.0.1", port=settings.port)
        finally:
            secret_file.unlink(missing_ok=True)
        return 0

    if args.command == "pair":
        data_dir_path = Path(args.data_dir) if args.data_dir else None
        settings = load_settings(data_dir=data_dir_path)
        secret_file = settings.data_dir / "admin.secret"

        if not secret_file.is_file():
            print("Jet 未运行，请先运行 jet serve")
            return 1

        admin_secret = secret_file.read_text(encoding="utf-8").strip()
        try:
            resp = httpx.post(
                f"http://127.0.0.1:{settings.port}/internal/pair-code",
                headers={"X-Jet-Admin": admin_secret},
                timeout=5.0,
            )
            if resp.status_code != 200:
                print(f"Jet 拒绝生成配对码（HTTP {resp.status_code}）：请确认 jet serve 与 jet pair 使用同一个数据目录")
                return 1
            data = resp.json()
            code = data.get("code")
            print(f"配对码：{code}（5 分钟内有效）")
            print("在插件设置页输入这个配对码")
            return 0
        except Exception:
            print("Jet 未运行，请先运行 jet serve")
            return 1

    if args.command == "stats":
        data_dir_path = Path(args.data_dir) if args.data_dir else None
        settings = load_settings(data_dir=data_dir_path)
        db_file = settings.data_dir / "jet.db"
        if not db_file.is_file():
            print("还没有数据")
            return 0

        conn = connect(settings.data_dir)
        try:
            cur = conn.execute("SELECT completeness, COUNT(*) as cnt FROM jobs GROUP BY completeness")
            counts = {row["completeness"]: row["cnt"] for row in cur.fetchall()}
            list_only = counts.get("list_only", 0)
            full = counts.get("full", 0)
            total_jobs = list_only + full

            quota = usage_today(conn, "me", purpose="judge")
            used, limit, remaining = quota["used"], quota["limit"], quota["remaining"]

            from jet.domain.judgements import rule_excluded_today as _rule_excluded_today

            rule_excluded_today = _rule_excluded_today(conn, "me")

            status_cur = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM judgements WHERE user_id = 'me' AND superseded_by IS NULL GROUP BY status"
            )
            status_counts = {row["status"]: row["cnt"] for row in status_cur.fetchall()}

            print(f"岗位总数: {total_jobs}（完整详情: {full}, 仅列表: {list_only}）")
            print(f"今日大模型调用: {used}/{limit}（剩余: {remaining}）")
            print(f"今日规则排除: {rule_excluded_today}")
            print("判断状态统计:")
            for st in ["queued", "running", "done", "failed", "quota_exhausted", "interrupted"]:
                print(f"  - {st}: {status_counts.get(st, 0)}")
            return 0
        finally:
            conn.close()

    if args.command == "eval":
        if not args.eval_command:
            eval_parser.print_help()
            return 1

        data_dir_path = Path(args.data_dir) if args.data_dir else None
        settings = load_settings(data_dir=data_dir_path)

        if args.eval_command == "run":
            if not (settings.data_dir / "jet.db").is_file():
                print("还没有数据：请先运行 jet serve，在 BOSS 页面点开岗位并在卡片上标注")
                return 1
            # 数据库可能还是旧结构（还没用新版 jet serve 启动过）：先迁移（会自动备份）
            migration_info = init_db(settings.data_dir)
            if migration_info:
                print(
                    f"已从结构版本 {migration_info.from_version} 迁移到 {migration_info.to_version}，"
                    f"备份：{migration_info.backup_filename}"
                )
                if migration_info.cleared:
                    for _, job_title, field_name, old_val in migration_info.cleared:
                        print(f"需要重新标注：{job_title} 的 {field_name}（原值：{old_val}）")
            raw_variants = [v.strip().upper() for v in args.variants.split(",") if v.strip()]
            unknown = [v for v in raw_variants if v not in ("A", "B", "C")]
            if unknown:
                # 组合 D（TypeSafe Jev）已在体检第 82 条删除
                print(f"没有这个评测组合：{', '.join(unknown)}（可选 A、B、C）")
                return 1
            try:
                _, table_str = run_eval(
                    settings,
                    variants=raw_variants,
                    max_calls=args.max_calls,
                )
                print(table_str)
                return 0
            except RuntimeError as e:
                print(str(e))
                return 1

        if args.eval_command == "report":
            try:
                _, table_str = report_eval(
                    settings,
                    run_id=args.run_id,
                )
                print(table_str)
                return 0
            except Exception as e:
                print(f"查看报告失败: {e}")
                return 1

        if args.eval_command == "export-jobs":
            if not (settings.data_dir / "jet.db").is_file():
                print("还没有数据")
                return 1
            init_db(settings.data_dir)
            try:
                out_path = Path(args.out)
                export_jobs(settings, out_path, limit=args.limit)
                print(f"已导出到 {out_path}")
                return 0
            except Exception as e:
                print(f"导出失败: {e}")
                return 1

        if args.eval_command == "import-reference":
            if not (settings.data_dir / "jet.db").is_file():
                print("还没有数据")
                return 1
            init_db(settings.data_dir)
            try:
                file_path = Path(args.file)
                if not file_path.is_file():
                    print(f"文件不存在: {file_path}")
                    return 1
                import_reference(settings, file_path, source_override=args.source)
                return 0
            except Exception as e:
                print(f"导入失败: {e}")
                return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
