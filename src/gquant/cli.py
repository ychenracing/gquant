"""Command-line entry point for research; there are no broker or order-placement commands."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .application.service import backtest
from .application.validation import validate_economics
from .infrastructure.acquisition import fetch_snapshot, request_windows
from .infrastructure.configuration import load_config
from .infrastructure.data import validate_snapshot


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="gquant", description="日线量化研究与人工决策支持")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("config", help="输出默认参数 JSON；参数含义见 docs/parameters.md")
    data = commands.add_parser("validate-data", help="校验完整 CSV 清单、哈希、行数、日期与行情值")
    data.add_argument("--data-dir", type=Path, default=Path("data"))
    for command in ("backtest", "validate"):
        action = commands.add_parser(
            command, help="连续账户回测" if command == "backtest" else "冻结参考经济验收与诊断"
        )
        action.add_argument("--config", type=Path, help="JSON 参数覆盖；未知字段报错")
        action.add_argument("--data-dir", type=Path, default=Path("data"))
        action.add_argument(
            "--output", type=Path, required=True, help="事务发布目录（不是单个文件）"
        )
        if command == "backtest":
            action.add_argument(
                "--interval", nargs=2, metavar=("START", "END"), help="截取连续账户，不重置起点"
            )
        else:
            action.add_argument(
                "--capital-scan", action="store_true", help="增加四参考、六本金诊断；不放宽标准门槛"
            )
            action.add_argument(
                "--standard-only",
                action="store_true",
                help="只运行标准参考和完整账户，不执行成本/历史诊断",
            )
    fetch = commands.add_parser("fetch-data", help="联网获取独立候选数据；不修改冻结 data/ 目录")
    fetch.add_argument("--start", type=date.fromisoformat, required=True)
    fetch.add_argument("--end", type=date.fromisoformat, required=True)
    fetch.add_argument("--output", type=Path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "config":
            print(json.dumps(load_config(), ensure_ascii=False, indent=2, allow_nan=False))
            return 0
        if args.command == "validate-data":
            print(json.dumps(validate_snapshot(args.data_dir), ensure_ascii=False))
            return 0
        if args.command == "fetch-data":
            if args.output.resolve() == Path("data").resolve():
                raise ValueError("fetch output must be separate from the frozen data directory")
            destination = fetch_snapshot(args.output, windows=request_windows(args.start, args.end))
            print(destination)
            return 0
        cfg = load_config(args.config)
        if args.command == "backtest":
            destination = backtest(
                cfg, args.data_dir, args.output, tuple(args.interval) if args.interval else None
            )
            print(destination)
            return 0
        destination, success = validate_economics(
            cfg,
            args.data_dir,
            args.output,
            diagnostics=not args.standard_only,
            capital_scan=args.capital_scan,
        )
        print(destination)
        return 0 if success else 1
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(f"gquant: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
