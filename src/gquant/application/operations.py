"""Manual-account initialization, continuation and transactional publication."""

from __future__ import annotations

import copy
import hashlib
import math
from datetime import date
from pathlib import Path
from typing import Any, cast

import pandas as pd

from gquant.config import Config
from gquant.infrastructure.artifacts import publish, read_latest
from gquant.infrastructure.configuration import validate_config
from gquant.infrastructure.data import Snapshot, admit_snapshot
from gquant.infrastructure.identity import identity
from gquant.market import indicators as ind_mod
from gquant.market.panels import build_panels
from gquant.portfolio.models import Account, Position
from gquant.risk.guard import PortfolioGuard
from gquant.risk.regime import RegimeMachine
from gquant.strategy.planner import DecisionContext, OrderPlanner

from .engine import BacktestEngine
from .service import report_metrics
from .state import EngineState


def _finite(value: object, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"invalid {label}")
    return number


def _whole(value: object, label: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"{label} must be an integer")
    number = float(value)
    if not math.isfinite(number) or not number.is_integer():
        raise ValueError(f"invalid {label}")
    result = int(number)
    if positive and result <= 0:
        raise ValueError(f"invalid {label}")
    return result


def _timestamp(value: object, label: str) -> pd.Timestamp:
    if not isinstance(value, str | date):
        raise ValueError(f"invalid {label}")
    try:
        day = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if pd.isna(day):
        raise ValueError(f"invalid {label}")
    return day


def _snapshot_prefix_sha256(
    snapshot: Snapshot,
    symbols: list[str],
    through: pd.Timestamp,
) -> str:
    """Hash admitted OHLCV history through one processed day, excluding future extensions."""
    hasher = hashlib.sha256()
    fields = ("date", "open", "high", "low", "close", "volume")
    for symbol in sorted(symbols):
        frame = snapshot.bars.get(symbol)
        if frame is None:
            raise ValueError(f"{symbol}: missing from admitted snapshot")
        prefix = frame.loc[frame["date"] <= through, list(fields)]
        if prefix.empty:
            raise ValueError(f"{symbol}: no admitted history through saved account state")
        hasher.update(symbol.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(str(len(prefix)).encode("ascii"))
        hasher.update(b"\0")
        for row in prefix.itertuples(index=False, name=None):
            hasher.update(str(pd.Timestamp(row[0]).date()).encode("ascii"))
            hasher.update(b"\0")
            for raw in row[1:]:
                token = "NA" if pd.isna(raw) else format(float(raw), ".17g")
                hasher.update(token.encode("ascii"))
                hasher.update(b"\0")
    return hasher.hexdigest()


def _state_account(state: EngineState) -> dict[str, object]:
    return {
        "as_of": str(state.last_processed_day.date())
        if state.last_processed_day is not None
        else None,
        "cash": state.account.cash,
        "positions": [
            {
                "symbol": p.symbol,
                "shares": p.shares,
                "sellable_shares": p.sellable_shares,
                "entry_price": p.entry_price,
                "entry_date": str(p.entry_date.date()),
                "peak_close": p.peak_close,
                "realized_pnl": p.realized_pnl,
            }
            for p in sorted(state.account.positions.values(), key=lambda p: p.symbol)
        ],
        "next_orders": [dict(order) for order in state.pending_orders],
        "reset_boundary": dict(state.reset_boundary) if state.reset_boundary is not None else None,
    }


def _initialize_from_snapshot(
    cfg: Config,
    snapshot: Snapshot,
    as_of: pd.Timestamp,
    account_spec: object,
    *,
    risk_reset: bool,
) -> EngineState:
    cfg = validate_config(cfg)
    if not risk_reset:
        raise ValueError("initial account takeover requires explicit --risk-reset")
    if not isinstance(account_spec, dict):
        raise ValueError("account input must be an object")
    bars = {symbol: snapshot.bars[symbol] for symbol in cfg["universe"]}
    panels = build_panels(bars)
    index = pd.DatetimeIndex(panels["close"].index)
    if as_of not in index:
        raise ValueError("account as_of must be an admitted trading day")
    if as_of < pd.Timestamp(cfg["start"]):
        raise ValueError("account as_of precedes configured start")

    cash = _finite(account_spec.get("cash"), "account cash")
    if cash < 0:
        raise ValueError("account cash must be non-negative")
    positions_raw = account_spec.get("positions", [])
    if not isinstance(positions_raw, list):
        raise ValueError("account positions must be a list")
    positions: dict[str, Position] = {}
    for raw in positions_raw:
        if not isinstance(raw, dict):
            raise ValueError("account position must be an object")
        symbol = str(raw.get("symbol", ""))
        if symbol not in cfg["universe"] or symbol in positions:
            raise ValueError("account positions must be distinct configured symbols")
        shares = _whole(raw.get("shares"), "position shares", positive=True)
        sellable = _whole(raw.get("sellable_shares", shares), "sellable shares")
        if not 0 <= sellable <= shares:
            raise ValueError("sellable shares must be within the held quantity")
        entry_price = _finite(raw.get("entry_price"), "entry price", positive=True)
        entry_date = _timestamp(raw.get("entry_date"), "entry date")
        if entry_date > as_of:
            raise ValueError("position entry_date is after account as_of")
        close_hist = panels["close"][symbol].loc[:as_of].dropna()
        if close_hist.empty or float(close_hist.iloc[-1]) <= 0:
            raise ValueError(f"{symbol}: no valid mark at account takeover")
        mark = float(close_hist.iloc[-1])
        positions[symbol] = Position(
            symbol=symbol,
            shares=shares,
            entry_price=entry_price,
            peak_close=max(mark, _finite(raw.get("peak_close", mark), "peak close", positive=True)),
            entry_date=entry_date,
            sellable_shares=sellable,
            realized_pnl=_finite(raw.get("realized_pnl", 0.0), "realized pnl"),
        )

    account = Account(cash=cash, positions=positions)
    marks = {
        symbol: float(panels["close"][symbol].loc[:as_of].dropna().iloc[-1]) for symbol in positions
    }
    equity = cash + sum(p.shares * marks[p.symbol] for p in positions.values())
    if not math.isfinite(equity) or equity <= 0:
        raise ValueError("account takeover equity must be positive")

    ind = ind_mod.compute_indicators(panels, cfg)
    mkt = ind_mod.market_metrics(panels, ind, cfg)
    regime = RegimeMachine(cfg)
    for day in mkt.index[mkt.index <= as_of]:
        regime.update(pd.Timestamp(day), cast(Any, mkt.loc[day]))
    guard = PortfolioGuard(cfg)
    target_gross = guard.record_equity(as_of, equity, regime.exposure_cap(), regime.state)
    planner = OrderPlanner(cfg)
    mkt_row = cast(Any, mkt.loc[as_of])
    ewi_ext = float(mkt_row["ewi"] / mkt_row["ewi_ma_regime"] - 1.0)
    close_row = cast(Any, panels["close"].loc[as_of])
    pending = planner.plan(
        DecisionContext(
            as_of,
            regime.state,
            target_gross,
            target_gross,
            equity,
            ewi_ext,
            panels,
            ind,
            close_row,
            marks,
        ),
        account,
        [],
    )
    account.events.append(f"{as_of.date()} 人工账户接管: 显式重置历史回撤/冷却状态")
    last_close: dict[str, float] = {}
    last_volume: dict[str, float] = {}
    for symbol in panels["close"].columns:
        close_series = panels["close"][symbol].loc[:as_of].dropna()
        volume_series = panels["volume"][symbol].loc[:as_of].dropna()
        if not close_series.empty and float(close_series.iloc[-1]) > 0:
            last_close[str(symbol)] = float(close_series.iloc[-1])
        if not volume_series.empty and float(volume_series.iloc[-1]) >= 0:
            last_volume[str(symbol)] = float(volume_series.iloc[-1])
    exposure = sum(p.shares * marks[p.symbol] for p in positions.values()) / equity
    return EngineState(
        last_processed_day=as_of,
        account=account,
        pending_orders=pending,
        regime=regime.export_state(),
        guard=guard.export_state(),
        planner=planner.export_state(),
        pulse_cooldown_left=0,
        prev_target_gross=target_gross,
        vol_equity_hist=[equity],
        corr_flat_until=pd.Timestamp("1900-01-01"),
        last_known_close=last_close,
        last_known_volume=last_volume,
        equity_dates=[as_of],
        equity_values=[equity],
        exposure_values=[exposure],
        regime_values=[regime.state],
        data_prefix_sha256=_snapshot_prefix_sha256(snapshot, list(cfg["universe"]), as_of),
        reset_boundary={
            "account_reset": True,
            "risk_reset": True,
            "as_of": str(as_of.date()),
            "reason": "manual_account_takeover",
        },
        reconciliations=[],
    )


def initialize_account_state(
    cfg: Config,
    data_dir: Path,
    as_of: pd.Timestamp,
    account_spec: object,
    *,
    risk_reset: bool,
) -> EngineState:
    return _initialize_from_snapshot(
        validate_config(cfg), admit_snapshot(data_dir), as_of, account_spec, risk_reset=risk_reset
    )


def parse_actual_events(
    raw: object, after: pd.Timestamp
) -> dict[pd.Timestamp, dict[str, list[dict[str, object]]]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("sessions"), list):
        raise ValueError("actual events must contain a sessions list")
    result: dict[pd.Timestamp, dict[str, list[dict[str, object]]]] = {}
    for session in raw["sessions"]:
        if not isinstance(session, dict):
            raise ValueError("actual session must be an object")
        day = _timestamp(session.get("date"), "actual session date")
        if day <= after or day in result:
            raise ValueError("actual session dates must be distinct and after saved state")
        fills = session.get("fills")
        if not isinstance(fills, list):
            raise ValueError("actual session must explicitly provide fills, including []")
        actions = session.get("corporate_actions", [])
        if not isinstance(actions, list):
            raise ValueError("corporate_actions must be a list")
        if not all(isinstance(item, dict) for item in fills + actions):
            raise ValueError("actual fill/action entries must be objects")
        result[day] = {
            "fills": [dict(item) for item in fills],
            "corporate_actions": [dict(item) for item in actions],
        }
    return result


def require_complete_actual_sessions(
    events: dict[pd.Timestamp, dict[str, list[dict[str, object]]]],
    trading_days: pd.DatetimeIndex,
    *,
    after: pd.Timestamp,
    end: pd.Timestamp,
) -> None:
    expected = {pd.Timestamp(day) for day in trading_days if after < pd.Timestamp(day) <= end}
    actual = set(events)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing:
        joined = ", ".join(str(day.date()) for day in missing)
        raise ValueError(f"missing authoritative broker sessions: {joined}")
    if unexpected:
        joined = ", ".join(str(day.date()) for day in unexpected)
        raise ValueError(f"unexpected broker sessions outside resume window: {joined}")
    if not expected:
        raise ValueError("resume end contains no trading sessions after saved state")


def publish_account_state(
    output: Path,
    cfg: Config,
    data_dir: Path,
    snapshot: Snapshot,
    state: EngineState,
    report: dict[str, object],
) -> Path:
    if state.last_processed_day is None:
        raise ValueError("account continuation state has no processed day")
    state.data_prefix_sha256 = _snapshot_prefix_sha256(
        snapshot, list(cfg["universe"]), state.last_processed_day
    )
    return publish(
        output,
        {
            "state.json": state.to_dict(),
            "account.json": _state_account(state),
            "next_orders.json": [dict(order) for order in state.pending_orders],
            "reconciliations.json": [dict(item) for item in state.reconciliations],
            "config.json": cfg,
            "identity.json": identity(cfg, data_dir, snapshot.info),
            "report.json": report,
        },
    )


def initialize_and_publish(
    cfg: Config,
    data_dir: Path,
    output: Path,
    as_of: pd.Timestamp,
    account_spec: object,
    *,
    risk_reset: bool,
) -> Path:
    cfg = validate_config(cfg)
    snapshot = admit_snapshot(data_dir)
    state = _initialize_from_snapshot(cfg, snapshot, as_of, account_spec, risk_reset=risk_reset)
    report: dict[str, object] = {
        "mode": "manual_account_takeover",
        "simulation_only": False,
        "last_processed_day": str(as_of.date()),
        "account_reset": True,
        "reset_boundary": dict(state.reset_boundary or {}),
    }
    return publish_account_state(output, cfg, data_dir, snapshot, state, report)


def resume_and_publish(
    state_root: Path,
    data_dir: Path,
    output: Path,
    *,
    end: str | None = None,
    actual_events: object | None = None,
) -> Path:
    bundle = read_latest(state_root)
    cfg = validate_config(bundle["config.json"])
    state = EngineState.from_dict(bundle["state.json"])
    if state.last_processed_day is None:
        raise ValueError("saved account state has no last_processed_day")
    if end is not None:
        raw = copy.deepcopy(dict(cfg))
        raw["end"] = str(end)
        cfg = validate_config(raw)
    requested_end = pd.Timestamp(cfg["end"])
    if requested_end <= state.last_processed_day:
        raise ValueError("resume end must be after saved account state")

    events = (
        parse_actual_events(actual_events, state.last_processed_day)
        if actual_events is not None
        else {}
    )
    snapshot = admit_snapshot(data_dir)
    if state.data_prefix_sha256 is None:
        raise ValueError("saved account state has no admitted data prefix identity")
    current_prefix = _snapshot_prefix_sha256(
        snapshot, list(cfg["universe"]), state.last_processed_day
    )
    if current_prefix != state.data_prefix_sha256:
        raise ValueError("admitted data history differs from saved account state")
    bars = {symbol: snapshot.bars[symbol] for symbol in cfg["universe"]}
    trading_days = pd.DatetimeIndex(build_panels(bars)["close"].index)
    require_complete_actual_sessions(
        events,
        trading_days,
        after=state.last_processed_day,
        end=requested_end,
    )

    engine = BacktestEngine(cfg, data_dir)
    result = engine.run(snapshot.bars, state=state, actual_events=events)
    final_state = engine.last_state
    if final_state is None:
        raise RuntimeError("engine did not publish a continuation state")
    report: dict[str, object] = {
        "mode": "manual_account_continuation",
        "simulation_only": False,
        "performance_since_reset": report_metrics(result),
        "account_reset": bool(final_state.reset_boundary),
        "reset_boundary": dict(final_state.reset_boundary or {}),
        "last_processed_day": (
            str(final_state.last_processed_day.date())
            if final_state.last_processed_day is not None
            else None
        ),
        "actual_sessions": [str(day.date()) for day in sorted(events)],
    }
    return publish_account_state(output, cfg, data_dir, snapshot, final_state, report)
