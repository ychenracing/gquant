"""Rotation selection, allocation risk caps and prior-close protective setup."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import pandas as pd

from gquant.config import Config
from gquant.portfolio.models import Order

_PARTIAL_SELL_REASONS = {"risk_off_trim", "rebalance_trim"}


def _forced_exit_symbols(pending_orders: list[Order]) -> set[str]:
    """完整退出才绕过迟滞；部分风险修剪仍保留候选资格。"""
    return {
        order["symbol"]
        for order in pending_orders
        if order["action"] == "sell" and order.get("reason", "") not in _PARTIAL_SELL_REASONS
    }


def _upgrade_partial_sells_for_dropped(
    target: list[str],
    positions: Mapping[str, object],
    pending_orders: list[Order],
) -> None:
    """若轮动决定彻底落榜，把同日部分减仓升级为一次完整退出。"""
    keep = set(target)
    for order in pending_orders:
        if order["action"] != "sell" or order.get("reason", "") not in _PARTIAL_SELL_REASONS:
            continue
        symbol = order["symbol"]
        if symbol in keep or symbol not in positions:
            continue
        shares = getattr(positions[symbol], "shares", None)
        if shares is not None:
            order["shares"] = int(shares)
        order["reason"] = "rotation_exit"


def stable_rotation_target(
    rf: pd.Series[Any],
    positions: Mapping[str, object],
    pending_orders: list[Order],
    ind: dict[str, pd.DataFrame],
    day: pd.Timestamp,
    cfg: Config,
) -> list[str]:
    """返回带轻量迟滞的 top-N 目标集。"""
    n = int(cfg["max_positions"])
    if n <= 0 or rf.empty:
        return []
    rc = cfg["rotation_contract"]
    raw_ranked = list(rf.sort_values(ascending=False).index)
    if not rc["hysteresis"]:
        target = raw_ranked[:n]
        _upgrade_partial_sells_for_dropped(target, positions, pending_orders)
        return target
    forced_sells = _forced_exit_symbols(pending_orders)
    ranked = [s for s in raw_ranked if s not in forced_sells]
    target = ranked[:n]
    if not positions or len(ranked) <= n:
        _upgrade_partial_sells_for_dropped(target, positions, pending_orders)
        return target
    rank_pos = {symbol: idx + 1 for idx, symbol in enumerate(ranked)}
    atr_row = ind["atr_pct"].loc[day]
    confirm_rows = [ind[name].loc[day] for name in rc["confirm_factors"]]
    incumbents = sorted(
        (s for s in positions if s in rank_pos and s not in forced_sells),
        key=lambda s: rank_pos[s],
    )
    for incumbent in incumbents:
        if incumbent in target or rank_pos[incumbent] > int(rc["hold_rank"]):
            continue
        newcomers = [s for s in target if s not in positions]
        if not newcomers:
            continue
        challenger = max(newcomers, key=lambda s: rank_pos[s])
        inc_atr = atr_row.get(incumbent, float("nan"))
        ch_atr = atr_row.get(challenger, float("nan"))
        valid_atr = [float(x) for x in (inc_atr, ch_atr) if pd.notna(x) and float(x) > 0]
        atr_scale = max(valid_atr) if valid_atr else float(cfg["rotation_atr_floor_pct"])
        threshold = float(rc["switch_edge_atr"]) * atr_scale
        edge = float(rf[challenger] - rf[incumbent])
        material_edge = edge >= threshold
        confirmed = bool(confirm_rows)
        for row in confirm_rows:
            inc = row.get(incumbent, float("nan"))
            ch = row.get(challenger, float("nan"))
            if pd.isna(inc) or pd.isna(ch) or float(ch) - float(inc) < threshold:
                confirmed = False
                break
        if not material_edge and not confirmed:
            target[target.index(challenger)] = incumbent
    deduped = []
    for symbol in sorted(target, key=lambda s: rank_pos.get(s, 10**9)):
        if symbol not in deduped:
            deduped.append(symbol)
    for symbol in ranked:
        if len(deduped) >= n:
            break
        if symbol not in deduped:
            deduped.append(symbol)
    final_target = deduped[:n]
    _upgrade_partial_sells_for_dropped(final_target, positions, pending_orders)
    return final_target


def armed_pair_stop_orders(
    positions: Mapping[str, object],
    decision_day: pd.Timestamp,
    close_row: pd.Series[Any],
    ind: dict[str, pd.DataFrame],
    market_ext: float,
    cfg: Config,
) -> list[dict[str, object]]:
    """Return next-session conditional stops using only information known at this close."""
    rc = cfg["rotation_contract"]
    if not rc["pair_stop"] or len(positions) != 2:
        return []
    if pd.isna(market_ext) or float(market_ext) < float(rc["pair_stop_market_ext"]):
        return []
    symbols = [str(symbol) for symbol in positions]
    known_close = close_row.reindex(symbols)
    if not bool((known_close > 0).all()):
        return []
    hist = ind["ret1"][symbols].loc[:decision_day].dropna(how="any").tail(20)
    if len(hist) < 10:
        return []
    pair_corr = float(cast(float, hist.corr().iloc[0, 1]))
    if pd.isna(pair_corr) or pair_corr < float(rc["pair_stop_corr20"]):
        return []
    return [
        {
            "symbol": symbol,
            "action": "sell",
            "shares": int(getattr(positions[symbol], "shares", 0)),
            "trigger": "low_at_or_below",
            "trigger_price": float(known_close[symbol]) * (1.0 + float(cfg["corr_sell_pct"])),
            "reason": "pair_stop",
        }
        for symbol in symbols
        if int(getattr(positions[symbol], "shares", 0)) > 0
    ]


def extended_pair_stop_prices(
    positions: Mapping[str, object],
    prev_day: pd.Timestamp,
    open_row: pd.Series[Any],
    low_row: pd.Series[Any],
    prev_close_row: pd.Series[Any],
    ind: dict[str, pd.DataFrame],
    market_ext: float,
    cfg: Config,
) -> dict[str, float]:
    """Return fills for prior-close conditional pair stops in the simulated replay."""
    armed = armed_pair_stop_orders(positions, prev_day, prev_close_row, ind, market_ext, cfg)
    result: dict[str, float] = {}
    for order in armed:
        symbol = str(order["symbol"])
        stop_level = float(order["trigger_price"])
        current_open = open_row.get(symbol)
        current_low = low_row.get(symbol)
        if (
            pd.isna(current_open)
            or pd.isna(current_low)
            or float(current_open) <= 0
            or float(current_low) <= 0
            or float(current_low) > stop_level
        ):
            continue
        result[symbol] = float(current_open) if float(current_open) <= stop_level else stop_level
    return result


def apply_rotation_risk_caps(
    alloc: dict[str, float],
    target_gross: float,
    cfg: Config,
    ind: dict[str, pd.DataFrame],
    day: pd.Timestamp,
) -> dict[str, float]:
    """在 ATR 基础仓位之上施加尾部损失与共同风险上限。"""
    if not alloc:
        return {}
    rc = cfg["rotation_contract"]
    adjusted = {s: max(0.0, float(w)) for s, w in alloc.items()}
    if rc["tail_risk"] and float(rc["per_name_loss_budget"]) > 0:
        ret1 = ind["ret1"].loc[:day]
        for symbol, weight in list(adjusted.items()):
            recent = ret1[symbol].dropna().tail(int(rc["tail_lookback"]))
            worst_loss = max(0.0, -float(recent.min())) if len(recent) else 0.0
            loss_distance = max(float(cfg["hard_stop_pct"]), worst_loss)
            adjusted[symbol] = min(
                weight,
                float(cfg["max_single_weight"]),
                float(rc["per_name_loss_budget"]) / loss_distance,
            )
    total = sum(adjusted.values())
    if total > target_gross > 0:
        scale = target_gross / total
        adjusted = {s: w * scale for s, w in adjusted.items()}
    if rc["common_risk"] and len(adjusted) > 1 and float(rc["common_vol_cap"]) > 0:
        symbols = list(adjusted)
        lookback = int(rc["common_vol_lookback"])
        hist = ind["ret1"][symbols].loc[:day].dropna(how="any").tail(lookback)
        if len(hist) >= max(5, lookback // 2):
            cov = hist.cov()
            weights = pd.Series(adjusted, dtype=float).reindex(symbols)
            variance = float(cast(float, weights.dot(cov.dot(weights))))
            if variance > 0:
                realized_vol = variance**0.5
                cap = float(rc["common_vol_cap"])
                if realized_vol > cap:
                    scale = cap / realized_vol
                    adjusted = {s: w * scale for s, w in adjusted.items()}
    return adjusted


def rotation_weights(
    rot_target: list[str],
    target_gross: float,
    cfg: Config,
    ind: dict[str, pd.DataFrame] | None = None,
    day: pd.Timestamp | None = None,
) -> dict[str, float]:
    """轮动 top-N 权重：等权诊断口径或生产 ATR 风险预算口径。"""
    if not rot_target or target_gross <= 0:
        return {}
    cap = cfg["max_single_weight"]
    if cfg["rotation_sizing"] == "atr_risk_budget":
        risk_pct = cfg["rotation_risk_pct"]
        floor_pct = cfg["rotation_atr_floor_pct"]
        if ind is None or day is None:
            raise ValueError("ATR allocation requires indicators and decision date")
        atr_pct_row = ind["atr_pct"].loc[day]
        alloc = {}
        for s in rot_target:
            a = atr_pct_row.get(s, float("nan"))
            if pd.isna(a) or a <= 0:
                a = floor_pct
            alloc[s] = min(cap, risk_pct / max(float(a), floor_pct))
        return apply_rotation_risk_caps(alloc, target_gross, cfg, ind, day)
    rel_weights = cfg["rotation_rank_weights"]
    n = len(rot_target)
    rel = [rel_weights[i] if i < len(rel_weights) else rel_weights[-1] for i in range(n)]
    total_rel = sum(rel)
    alloc = {s: target_gross * rel[i] / total_rel for i, s in enumerate(rot_target)}
    for _ in range(n):
        overflow = sum(v - cap for v in alloc.values() if v > cap)
        if overflow <= 1e-12:
            break
        room = {s: cap - v for s, v in alloc.items() if v < cap - 1e-12}
        room_total = sum(room.values())
        if room_total <= 1e-12:
            break
        add = min(overflow, room_total)
        for s in alloc:
            if alloc[s] > cap:
                alloc[s] = cap
            elif s in room:
                alloc[s] += add * room[s] / room_total
    return {s: min(v, cap) for s, v in alloc.items()}
