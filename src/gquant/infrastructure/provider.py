#!/usr/bin/env python3
"""Tencent daily-bar requests, board-unit conversion and snapshot validation."""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path("data")

# 拉取窗口 (两段拼接, 每段 <=500 根, 覆盖 2023-12 ~ 2026-09)
WINDOWS = [("2023-12-01", "2025-06-30"), ("2025-06-01", "2026-09-05")]

# 中位名义成交额的宽松单位错误筛查上限（元），不是成交容量保证。
MAX_PLAUSIBLE_TURNOVER = 1000e8

# 待请求的 AI 科技产业链股票与市场参考指数。
SYMBOLS: dict[str, str] = {
    # 光通信、半导体设备与材料
    "sz300308": "中际旭创",
    "sz300502": "新易盛",
    "sz300394": "天孚通信",
    "sh688498": "源杰科技",
    "sz002281": "光迅科技",
    "sh601869": "长飞光纤",
    "sh688008": "澜起科技",
    "sh603986": "兆易创新",
    "sz300223": "北京君正",
    "sh688256": "寒武纪",
    "sh688041": "海光信息",
    "sh688347": "华虹宏力",
    "sz002371": "北方华创",
    "sh688012": "中微公司",
    "sh688072": "拓荆科技",
    "sh688082": "盛美上海",
    "sh688120": "华海清科",
    "sh688037": "芯源微",
    "sh688361": "中科飞测",
    "sz300604": "长川科技",
    "sz002409": "雅克科技",
    "sh688300": "联瑞新材",
    "sz300054": "鼎龙股份",
    "sh688019": "安集科技",
    "sz300666": "江丰电子",
    "sh688268": "华特气体",
    "sz300776": "帝尔激光",
    # 计算基础设施与产业链配套
    "sh603019": "中科曙光",
    "sh605358": "立昂微",
    "sh600183": "生益科技",
    "sz000977": "浪潮信息",
    "sz000988": "华工科技",
    "sz002463": "沪电股份",
    "sz300655": "晶瑞电材",
    "sz300236": "上海新阳",
    "sh600206": "有研新材",
    "sh600487": "亨通光电",
    "sh603256": "宏和科技",
    "sh688519": "晶合集成",
    "sh688825": "长鑫科技",
    # 指数
    "sh000300": "沪深300",
    "sh000682": "中证AI",
}
INDEX_CODES = {"sh000300", "sh000682"}
MIN_ROWS = 200  # 上市过晚的标的跳过

BASE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _fetch_window(symbol: str, start: str, end: str) -> list[list[str]]:
    adjust = "" if symbol in INDEX_CODES else "qfq"
    url = f"{BASE_URL}?param={symbol},day,{start},{end},500,{adjust}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    # URL is constructed from a fixed HTTPS origin, not a caller-provided scheme.
    with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
        payload = json.loads(resp.read().decode("utf-8"))
    node = payload.get("data", {}).get(symbol, {})
    rows = node.get("qfqday") or node.get("day") or []
    if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
        raise ValueError("provider daily bars must be rows")
    return rows


def fetch_symbol(
    symbol: str, name: str, windows: list[tuple[str, str]] | None = None
) -> pd.DataFrame | None:
    rows_by_date: dict[str, list[str]] = {}
    for start, end in WINDOWS if windows is None else windows:
        for attempt in range(3):
            try:
                rows = _fetch_window(symbol, start, end)
                break
            except Exception as exc:  # noqa: BLE001
                print(f"  retry{attempt + 1} {symbol}: {type(exc).__name__}: {exc}", flush=True)
                time.sleep(2)
        else:
            print(f"  [FAIL] {symbol} {name}: 窗口 {start}~{end} 三次重试失败", flush=True)
            return None
        for row in rows:
            # 腾讯列序: date, open, close, high, low, volume；单位按板块不同，见下方换算
            rows_by_date[row[0]] = row
    if not rows_by_date:
        print(f"  [SKIP] {symbol} {name}: 无数据 (可能未上市)", flush=True)
        return None
    records = sorted(rows_by_date.values(), key=lambda r: r[0])
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime([r[0] for r in records]),
            "open": [float(r[1]) for r in records],
            "close": [float(r[2]) for r in records],
            "high": [float(r[3]) for r in records],
            "low": [float(r[4]) for r in records],
            # Tencent snapshot units differ by board: STAR=shares, other A-shares=lots.
            # Indices are not traded by BacktestEngine, so their liquidity is deliberately unmodelled.
            "volume": [
                0.0
                if symbol in INDEX_CODES
                else (float(r[5]) if symbol.startswith("sh688") else float(r[5]) * 100.0)
                for r in records
            ],
        }
    )
    frame["amount"] = frame["volume"] * frame["close"]
    frame["name"] = name
    return frame


def validate(frame: pd.DataFrame, symbol: str) -> list[str]:
    """冻结前校验价格与实际用于成交约束的股数 volume。"""
    issues: list[str] = []
    px = frame[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(px).all():
        issues.append("NaN/Inf 价格")
    if (px <= 0).any():
        issues.append("非正价格")
    if (frame["high"] < frame["low"]).any():
        issues.append("high<low")
    if (frame["high"] < frame[["open", "close"]].max(axis=1)).any():
        issues.append("high<max(open,close)")
    if (frame["low"] > frame[["open", "close"]].min(axis=1)).any():
        issues.append("low>min(open,close)")
    if frame["date"].duplicated().any():
        issues.append("重复日期")
    for col in ("volume", "amount"):
        values = frame[col].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            issues.append(f"NaN/Inf {col}")
        if (values < 0).any():
            issues.append(f"负 {col}")
    if symbol not in INDEX_CODES:
        proxy = float(frame["amount"].median())
        if proxy > MAX_PLAUSIBLE_TURNOVER:
            issues.append(
                f"中位名义成交额代理 {proxy / 1e8:.0f}亿元 超过物理上限 "
                f"{MAX_PLAUSIBLE_TURNOVER / 1e8:.0f}亿元"
            )
    return [f"{symbol}: {issue}" for issue in issues]
