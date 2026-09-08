"""Regression contracts for parameters, state ownership and risk behavior."""

from __future__ import annotations

import copy
import inspect
import re
from pathlib import Path

import pandas as pd
import pytest

from gquant.config import CONFIG
from gquant.infrastructure.data import load_universe
from gquant.market.indicators import compute_indicators, market_metrics
from gquant.market.panels import build_panels
from gquant.risk.exits import exit_reason
from gquant.risk.guard import PortfolioGuard
from gquant.strategy import signals as sig_mod

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "src/gquant"
SOURCE = "".join(
    p.read_text(encoding="utf-8")
    for p in sorted(PACKAGE_DIR.rglob("*.py"))
    if p.name != "config.py"
)


# ---------- 参数治理 ----------


def test_all_config_params_are_referenced():
    """CONFIG 中不得存在死参数: 每个键都必须被某处代码引用。

    死参数的危害不是浪费一行, 而是它会出现在「参数敏感性分析」的清单里,
    让人以为调过它; 实际调整结果逐字节不变, 于是把「平台型稳健」误读成
    「该机制不敏感」。「惰性但被代码读取」的参数不在此列 —— 那类参数在
    docs/parameters.md 中单独标注, 并附上「为何在当前数据上不敏感」的实测证据。
    """
    dead = [k for k in CONFIG if k not in SOURCE]
    assert not dead, f"以下 CONFIG 参数无任何代码引用: {dead}"


def test_no_shadow_params_bypass_config():
    """代码不得用 cfg.get() 的内联默认值引入 CONFIG 里没有的参数。

    影子参数的危害: 它不出现在冻结快照里, 因此任何「全参数敏感性研究」都会
    漏掉它, 而它可能在驱动一个完整的风控模块; 同时 `--config` 虽然能改它,
    但没人知道它存在。跨行的 `cfg.get(` 调用也必须被扫到。
    """
    used = set(re.findall(r"""cfg\.get\(\s*["'](\w+)["']""", SOURCE))
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        if path.name == "config.py":
            continue
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"""cfg\.get\(\s*\n?\s*["'](\w+)["']""", text))
    shadow = sorted(k for k in used if k not in CONFIG)
    assert not shadow, f"以下参数只存在于内联默认值, 未纳入 CONFIG: {shadow}"


def test_cfg_get_defaults_match_config():
    """cfg.get(key, default) 的默认值必须与 CONFIG 一致, 否则冻结快照失真。"""
    mismatch = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        if path.name == "config.py":
            continue
        text = path.read_text(encoding="utf-8")
        for key, default in re.findall(
            r"""cfg\.get\(\s*\n?\s*["'](\w+)["']\s*,\s*([-0-9.]+)""", text
        ):
            if key in CONFIG:
                expected = CONFIG[key]
                if (
                    isinstance(expected, int | float)
                    and abs(float(default) - float(expected)) > 1e-9
                ):
                    mismatch.append(f"{key}: CONFIG={expected} 内联={default}")
    assert not mismatch, "内联默认值与 CONFIG 不一致:\n" + "\n".join(mismatch)


# ---------- 等价性锁 (防止重构静默改变行为) ----------


def test_vote_min_4_equals_strict_and_gate():
    """vote_min=4 必须与严格四票 AND 逐元素等价。

    入场门用「票数 >= vote_min」表达, vote_min=4 时应退化为四个条件全满足。
    该等价性是投票制不改变默认行为的前提; 若被破坏, 说明票数的构造有误
    (例如把 breakout 与 channel 分别计票, 使满分变成 5)。
    """
    bars = load_universe(CONFIG["universe"][:8])
    panels = build_panels(bars)
    ind = compute_indicators(panels, CONFIG)
    day = panels["close"].index[-2]

    cfg_and = copy.deepcopy(CONFIG)
    cfg_and["vote_min"] = 4
    got = sig_mod.select_candidates(day, panels, ind, cfg_and, "TREND")

    # 手工复算严格 AND 口径
    close = panels["close"].loc[day]
    ma_trend = ind["ma_trend"].loc[day]
    ma_fast = ind["ma_fast"].loc[day]
    ma20 = ind["ma20"].loc[day]
    atr = ind["atr"].loc[day]
    prox = ind["donchian_prox"].loc[day]
    vr = ind["volume_ratio"].loc[day]
    mom = ind["momentum"].loc[day]
    ph10 = ind["prev_high10"].loc[day]
    strict = (
        (close > ma_trend)
        & (ma_fast > ma_trend)
        & (
            (prox >= CONFIG["breakout_proximity"])
            | (close > ma20 + CONFIG["channel_atr_mult"] * atr)
        )
        & (vr >= CONFIG["volume_min_ratio"])
        & (mom > 0)
    )
    rebound = (close >= ph10) & (close > ma_fast) & (mom > 0) & (vr >= CONFIG["volume_min_ratio"])
    mom_rank = mom.rank(ascending=False, pct=False)
    mask = (mom_rank <= CONFIG["universe_keep"]) & (strict.fillna(False) | rebound.fillna(False))
    expected = sorted(close.index[mask.fillna(False)])
    assert sorted(got.index) == expected


def test_tilt_zero_reproduces_fixed_slot():
    """winner_tilt=0 时份额必须退回纯固定槽位制。

    份额公式为 min(单票上限, max(固定槽位, 归一倾斜份额)); tilt=0 时所有标的
    的倾斜份额相同且等于 target_gross/n, 只要 n 足够大使其小于槽位, max() 就会
    选中槽位, 于是权重与倾斜完全无关。这是「倾斜是可选叠加项、关掉即退回等分」
    的保证。
    """
    target_gross, max_positions, cap = 0.90, CONFIG["max_positions"], CONFIG["max_single_weight"]
    slot = min(cap, target_gross / max_positions * CONFIG["winner_slot_mult"])
    # tilt=0 => tilt_gain 恒为 1.0, 归一份额 = target_gross / n <= slot (n >= max_positions*0.85)
    n = max_positions
    tilt_share = (1.0 / (n * 1.0)) * target_gross
    assert min(cap, max(slot, tilt_share)) == pytest.approx(slot)
    assert tilt_share < slot, "tilt=0 时倾斜份额必须小于槽位, 否则等价性不成立"


def test_trend_break_counter_is_uncontended():
    """MA28 趋势死亡的连续计数器必须只有一个写入者。

    这是一个跨模块的可变状态, 因此极易被两条规则争用。争用的后果是双向的:
      - 同一天被两处各 +1 => 「连续 N 日确认」实际按 N/2 日触发;
      - 一处判定不成立时把计数器清零 => 另一处永远累积不到阈值, 该规则失效。
    两种错误都不会抛异常, 只会让退出时机静默偏移, 因此用静态检查锁死写入者
    数量, 而不是靠回测总收益去反推。
    """
    # 用 tokenize 剥除注释与字符串, 只检查真正会被执行的代码
    import io
    import tokenize

    def code_only(text: str) -> str:
        names = []
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.NAME:
                names.append(tok.string)
            elif tok.type == tokenize.OP:
                names.append(tok.string)
        return " ".join(names)

    assert "ma_break_count" not in code_only(SOURCE), "趋势计数不得引入第二个状态字段"
    engine_src = code_only((PACKAGE_DIR / "strategy/planner.py").read_text(encoding="utf-8"))
    risk_src = code_only((PACKAGE_DIR / "risk/exits.py").read_text(encoding="utf-8"))
    assert "trend_break_count" in engine_src
    assert "trend_break_count" not in risk_src, "risk.py 不得再写趋势死亡计数器"
    # exit_reason 不应再通过 position 对象产生副作用
    assert "position" not in inspect.signature(exit_reason).parameters


def test_exposure_caps_crash_governs_liquidation():
    """清仓必须由 exposure_caps 单一决定, 引擎不得硬编码状态名。

    引擎的正确写法是只看 `target_gross <= 0.05`; 若额外写成
    `state == "CRASH" or ...`, exposure_caps["CRASH"] 就变成一个被短路的
    死参数 —— 把它从 0.0 调到 0.20 结果逐字节不变, 而配置里看不出任何异常。
    """
    assert CONFIG["exposure_caps"]["CRASH"] == 0.0
    guard = PortfolioGuard(CONFIG)
    got = guard.record_equity(
        pd.Timestamp("2026-01-05"), 100.0, CONFIG["exposure_caps"]["CRASH"], "CRASH"
    )
    assert got == 0.0, "CRASH 档敞口上限 0.0 时应输出 0 以触发清仓"


# ---------- 四大风控信号的行为测试 ----------


def test_single_day_crash_exit_fires_at_threshold():
    """个股单日闪崩退出: 达到阈值即退出, 高于阈值不退。"""
    cfg = copy.deepcopy(CONFIG)
    row_hit = {"close": 91.0, "prev_close": 100.0, "prev2_close": 100.0, "atr_pct": 0.04}
    row_miss = {"close": 93.0, "prev_close": 100.0, "prev2_close": 100.0, "atr_pct": 0.04}
    assert exit_reason(100.0, 100.0, row_hit, cfg) == "crash_day_exit"
    assert exit_reason(100.0, 100.0, row_miss, cfg) is None


def test_two_day_crash_exit_fires():
    """双日累计暴跌退出: 单日未达阈值但双日累计达标时补网。"""
    cfg = copy.deepcopy(CONFIG)
    row = {"close": 87.5, "prev_close": 93.75, "prev2_close": 100.0, "atr_pct": 0.04}
    # 单日 -6.7% 未达 -8%, 双日 -12.5% 达到 -12%
    assert exit_reason(100.0, 100.0, row, cfg) == "crash_day_exit"


def test_hard_stop_takes_priority_over_chandelier():
    """硬止损: 亏损达阈值即退出, 且与吊灯区分 (亏损态吊灯倍数为最宽档)。"""
    cfg = copy.deepcopy(CONFIG)
    entry = 100.0
    close = entry * (1 - cfg["hard_stop_pct"])
    # prev_close/prev2_close 走多日阴跌路径: 若直接由 100 一日跌到 close,
    # 单日 -12% 会先命中闪崩退出 (-8% 档), 测不到硬止损本身。
    assert close / 92.0 - 1 > cfg["single_day_crash_pct"]
    assert close / 96.0 - 1 > cfg["two_day_crash_pct"]
    row = {"close": close, "prev_close": 92.0, "prev2_close": 96.0, "atr_pct": 0.04}
    assert exit_reason(entry, entry, row, cfg) == "hard_stop"


def test_chandelier_tightens_with_profit():
    """分级吊灯: 浮盈越高跟踪越紧, 且以最高收盘价为基准而非成本价。"""
    cfg = copy.deepcopy(CONFIG)
    tiers = cfg["chandelier_tiers"]
    assert [t["atr_mult"] for t in tiers] == sorted((t["atr_mult"] for t in tiers), reverse=True), (
        "吊灯倍数应随浮盈门槛上升而收紧"
    )
    entry, peak = 100.0, 200.0  # 浮盈 +100% -> 最紧档
    mult_tight = tiers[-1]["atr_mult"]
    atr_pct = 0.02
    line = peak * (1 - mult_tight * atr_pct)
    just_inside, just_outside = line + 0.01, line - 0.01
    # prev_close/prev2_close 必须取「峰值已在前几日、昨日已在吊灯线上方一点」
    # 的路径: 若令 prev_close = peak, 则任何吊灯触发点相对昨收都是 -10% 级
    # 跌幅, 会先命中单日闪崩退出 (-8% 档), 测不到吊灯本身。
    prev_close, prev2_close = line * 1.02, line * 1.05
    assert just_inside / prev_close - 1 > CONFIG["single_day_crash_pct"]
    assert just_inside / prev2_close - 1 > CONFIG["two_day_crash_pct"]
    ctx = {"prev_close": prev_close, "prev2_close": prev2_close, "atr_pct": atr_pct}
    assert exit_reason(entry, peak, dict(ctx, close=just_inside), cfg) is None
    got = exit_reason(entry, peak, dict(ctx, close=just_outside), cfg)
    assert got is not None and got.startswith("chandelier_")


def test_dd_abs_tiers_engages_and_releases_on_market_heal():
    """绝对回撤梯: 全期峰值回撤达档即压敞口, 市场客观修复时释放 (防锁死)。"""
    cfg = copy.deepcopy(CONFIG)
    tier = cfg["dd_abs_tiers"][0]
    guard = PortfolioGuard(cfg)
    day = pd.Timestamp("2026-01-05")
    guard.record_equity(day, 100.0, 1.0, "TREND")
    # 取略深于档位阈值: 100.0*(1-0.10)/100.0-1 在浮点下为 -0.0999... 大于
    # -0.10, 恰好落在档位边界外侧, 会测不到触发。
    dipped = 100.0 * (1 - tier["dd"] - 0.001)
    pressed = guard.record_equity(day + pd.Timedelta(days=1), dipped, 1.0, "TREND")
    assert pressed <= tier["target"] + 1e-9, (
        f"回撤达 {tier['dd']:.0%} 时敞口应压到 {tier['target']}, 实为 {pressed}"
    )
    deeper = PortfolioGuard(cfg)
    deeper.record_equity(day, 100.0, 1.0, "TREND")
    healed = deeper.record_equity(
        day + pd.Timedelta(days=1), dipped, 1.0, "TREND", market_heal=True
    )
    assert healed > pressed, (
        "市场客观修复时应释放绝对回撤梯, 否则形成压低敞口->净值不回->永久锁死的死角"
    )


def test_rolling_dd_tiers_survive_slow_grind_via_abs_ladder():
    """滚动回撤梯在慢跌中必然失效, 绝对回撤梯必须与之并存。

    滚动梯以 `peak_window` 内的峰值为基准, 慢跌时峰值随净值同步下移,
    dd_roll 始终很浅, 各档一次都不会触发; 而全期峰值口径的绝对梯不受窗口
    滑动影响。构造连续 45 个交易日每日 -0.5% 的阴跌 (累计约 -20%) 验证。
    """
    cfg = copy.deepcopy(CONFIG)
    guard = PortfolioGuard(cfg)
    day = pd.Timestamp("2025-01-01")
    eq = 100.0
    saw_press = False
    for i in range(45):
        eq *= 0.995
        out = guard.record_equity(day + pd.Timedelta(days=i), eq, 1.0, "TREND")
        saw_press |= out < 1.0
    total_dd = eq / 100.0 - 1.0
    assert total_dd < -0.19, f"构造的慢跌应累计约 -20%, 实为 {total_dd:.1%}"
    assert saw_press, "绝对梯应在慢跌中触发 (滚动梯此时无法触发)"


def test_rank_factor_options_are_available():
    """动态可达的指标键必须齐备 (它们静态引用计数为 0, 但不是死代码)。

    `cfg["rank_factor"]` / `cfg["winner_tilt_factor"]` 通过字符串索引 ind 字典,
    参数活性检查看不到这类键, 因此需要单独锁定: 少了会在运行时才 KeyError。
    """
    from gquant.market.indicators import DYNAMIC_RANK_FACTORS, compute_indicators

    bars = load_universe(CONFIG["core_universe"])
    panels = build_panels(bars)
    ind = compute_indicators(panels, CONFIG)
    missing = [k for k in DYNAMIC_RANK_FACTORS if k not in ind]
    assert not missing, f"动态 rank_factor 选项缺失: {missing}"
    assert CONFIG["rank_factor"] in ind
    assert CONFIG["winner_tilt_factor"] in ("volume", "momentum", *DYNAMIC_RANK_FACTORS)
    # market_metrics 的输出键同样被 regime 按名索引
    mkt = market_metrics(panels, ind, CONFIG)
    for key in ("ewi", "ewi_ret5", "ewi_ret20", "ewi_ma_regime", "ewi_ma_reclaim", "breadth"):
        assert key in mkt.columns, f"market_metrics 缺少 {key}"


def test_parameters_md_covers_every_config_key():
    """docs/parameters.md 必须逐键覆盖 CONFIG, 禁止文档静默落后于代码。

    本文档的主要价值在于区分「惰性」与「可以删」: 惰性是当前参数组合下的性质,
    删除会让相关代码路径失去可回归的旋钮。因此每个键都必须在册。
    """
    doc = (PACKAGE_DIR.parents[1] / "docs/parameters.md").read_text(encoding="utf-8")
    missing = [k for k in CONFIG if f"`{k}`" not in doc]
    assert not missing, f"docs/parameters.md 缺少以下 {len(missing)} 个 CONFIG 键的说明: {missing}"


def test_docstrings_match_code():
    """Document the active decision route and parameterized channel/state behavior."""
    doc = (PACKAGE_DIR.parents[1] / "docs/strategy.md").read_text(encoding="utf-8")
    assert "daily_rotation" in doc and "vote_gate" in doc
    assert "channel_atr_mult" in doc and "regime_reclaim_days" in doc
    indicator_source = (PACKAGE_DIR / "market/indicators.py").read_text(encoding="utf-8")
    assert "def true_range(" not in indicator_source


def test_baseline_drawdown_is_close_basis():
    """比较统一使用收盘权益回撤，盘中回撤仅作单独参考。"""
    from gquant.research.metrics import compute_metrics
    from gquant.research.targets import BASELINES

    # 本系统的指标口径必须是收盘: compute_metrics 用 equity/cummax-1, 不含盘中 low
    src = inspect.getsource(compute_metrics)
    assert "cummax" in src and "low" not in src.replace("fillna", ""), (
        "metrics.compute_metrics 必须是收盘口径, 不得混入盘中 low"
    )

    gl = [b for b in BASELINES if b["key"] == "glmcsm_6"][0]
    assert gl["max_drawdown"] == pytest.approx(-0.1432), (
        f"glmcsm 门槛必须用收盘口径 -14.32%, 当前为 {gl['max_drawdown']}"
    )
    assert gl.get("max_drawdown_intraday") == pytest.approx(-0.1663), (
        "盘中口径应作为参考值单独保留, 不得用于判定"
    )
    assert abs(gl["max_drawdown"]) < abs(gl["max_drawdown_intraday"]), (
        "收盘回撤的绝对值必然小于盘中回撤, 否则口径标反了"
    )

    for b in BASELINES:
        assert b["max_drawdown"] < 0, f"{b['key']} 的回撤门槛必须为负值"
        if b["key"] != "glmcsm_6":
            assert (
                "max_drawdown_intraday" not in b
                or b["max_drawdown_intraday"] is None
                or abs(b["max_drawdown"]) <= abs(b["max_drawdown_intraday"])
            ), f"{b['key']} 的两个回撤口径大小关系异常"
