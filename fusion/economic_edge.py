"""经济表现层: 只处理边缘换仓与集中风险，不产生新的主排序信号。

设计边界:
- `ret63` 仍是唯一主排序；相邻周期只在排名接近时确认是否值得换仓。
- 完整风险退出永远优先；组合层的部分减仓不应被误读为“该股票必须退出”。
- 尾部风险与共同风险只会缩小仓位，绝不把低风险仓位放大到原目标以上。
"""
from __future__ import annotations

from typing import Dict, Mapping

import pandas as pd


_PARTIAL_SELL_REASONS = {"risk_off_trim", "rebalance_trim"}


def _forced_exit_symbols(pending_orders: list[dict]) -> set[str]:
    """完整退出才绕过迟滞；部分风险修剪仍保留候选资格。"""
    return {
        order["symbol"]
        for order in pending_orders
        if order["action"] == "sell"
        and order.get("reason", "") not in _PARTIAL_SELL_REASONS
    }


def _upgrade_partial_sells_for_dropped(
    target: list[str],
    positions: Mapping[str, object],
    pending_orders: list[dict],
) -> None:
    """若轮动决定彻底落榜，把同日部分减仓升级为一次完整退出。

    这样可避免 engine 看到“已有卖单”后跳过 rotation_exit，留下不该继续持有的
    残仓；同时把两张同开盘卖单合成一张，避免重复最低佣金。
    """
    keep = set(target)
    for order in pending_orders:
        if order["action"] != "sell":
            continue
        if order.get("reason", "") not in _PARTIAL_SELL_REASONS:
            continue
        symbol = order["symbol"]
        if symbol in keep or symbol not in positions:
            continue
        shares = getattr(positions[symbol], "shares", None)
        if shares is not None:
            order["shares"] = int(shares)
        order["reason"] = "rotation_exit"


def stable_rotation_target(
    rf: pd.Series,
    positions: Mapping[str, object],
    pending_orders: list[dict],
    ind: Dict[str, pd.DataFrame],
    day: pd.Timestamp,
    cfg: dict,
) -> list[str]:
    """返回带轻量迟滞的 top-N 目标集。

    当前持仓若只从 top-N 滑到 `hold_rank`，且新挑战者相对 ret63 优势小于
    `switch_edge_atr × ATR%`，则先保留 incumbent。ret50 / ret80 只有在各自也
    达到同一 ATR 归一化的实质优势时才算确认。完整风险退出不受迟滞保护。
    """
    n = int(cfg["max_positions"])
    if n <= 0 or rf.empty:
        return []

    rc = cfg["rotation_contract"]
    raw_ranked = list(rf.sort_values(ascending=False).index)
    if not rc["hysteresis"]:
        return raw_ranked[:n]
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

        inc_atr = atr_row.get(incumbent)
        ch_atr = atr_row.get(challenger)
        valid_atr = [
            float(x) for x in (inc_atr, ch_atr)
            if pd.notna(x) and float(x) > 0
        ]
        atr_scale = max(valid_atr) if valid_atr else float(cfg["rotation_atr_floor_pct"])
        threshold = float(rc["switch_edge_atr"]) * atr_scale
        edge = float(rf[challenger] - rf[incumbent])
        material_edge = edge >= threshold

        confirmed = bool(confirm_rows)
        for row in confirm_rows:
            inc = row.get(incumbent)
            ch = row.get(challenger)
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


def extended_pair_stop_prices(
    positions: Mapping[str, object],
    prev_day: pd.Timestamp,
    open_row: pd.Series,
    low_row: pd.Series,
    prev_close_row: pd.Series,
    ind: Dict[str, pd.DataFrame],
    market_ext: float,
    cfg: dict,
) -> Dict[str, float]:
    """Return independent pre-committed stops for an overheated correlated pair.

    Setup is decided entirely at the previous close: the market equal-weight index must be
    sufficiently extended above its regime MA and the two overnight holdings must have high
    20-day return correlation. Once armed, each name owns its own pre-committed stop using
    ``corr_sell_pct``. A gap through that stop fills at the opening auction; otherwise the
    stop level is used. The other name's later intraday path is never consulted.
    """
    rc = cfg["rotation_contract"]
    if not rc["pair_stop"] or len(positions) != 2:
        return {}
    if pd.isna(market_ext) or float(market_ext) < float(rc["pair_stop_market_ext"]):
        return {}

    symbols = list(positions)
    prev_close = prev_close_row.reindex(symbols)
    current_open = open_row.reindex(symbols)
    current_low = low_row.reindex(symbols)
    # Previous-close setup needs both names, but today's executable bar is local
    # to each stop. A missing bar in the other name cannot revoke this protection.
    if not bool((prev_close > 0).all()):
        return {}
    valid = (current_open > 0) & (current_low > 0)

    hist = ind["ret1"][symbols].loc[:prev_day].dropna(how="any").tail(20)
    if len(hist) < 10:
        return {}
    pair_corr = float(hist.corr().iloc[0, 1])
    if pd.isna(pair_corr) or pair_corr < float(rc["pair_stop_corr20"]):
        return {}

    stop_levels = prev_close * (1.0 + float(cfg["corr_sell_pct"]))
    return {
        symbol: float(
            current_open[symbol]
            if current_open[symbol] <= stop_levels[symbol]
            else stop_levels[symbol]
        )
        for symbol in symbols
        if valid[symbol] and current_low[symbol] <= stop_levels[symbol]
    }


def apply_rotation_risk_caps(
    alloc: Dict[str, float],
    target_gross: float,
    cfg: dict,
    ind: Dict[str, pd.DataFrame],
    day: pd.Timestamp,
) -> Dict[str, float]:
    """在 ATR 基础仓位之上施加尾部损失与共同风险上限。"""
    if not alloc:
        return {}
    rc = cfg["rotation_contract"]
    adjusted = {s: max(0.0, float(w)) for s, w in alloc.items()}

    if rc["tail_risk"] and float(rc["per_name_loss_budget"]) > 0:
        ret1 = ind["ret1"].loc[:day]
        for symbol, weight in list(adjusted.items()):
            recent = ret1[symbol].dropna().tail(int(rc["tail_lookback"]))
            worst_loss = 0.0
            if len(recent):
                worst_loss = max(0.0, -float(recent.min()))
            loss_distance = max(float(cfg["hard_stop_pct"]), worst_loss)
            loss_cap = float(rc["per_name_loss_budget"]) / loss_distance
            adjusted[symbol] = min(weight, float(cfg["max_single_weight"]), loss_cap)

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
            variance = float(weights.dot(cov.dot(weights)))
            if variance > 0:
                realized_vol = variance ** 0.5
                cap = float(rc["common_vol_cap"])
                if realized_vol > cap:
                    scale = cap / realized_vol
                    adjusted = {s: w * scale for s, w in adjusted.items()}

    return adjusted
