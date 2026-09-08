# 参数手册

运行 `python -m gquant config` 可导出完整参数；定义位于 `src/gquant/config.py`，应用入口会校验未知字段、类型、有限性和相互约束。修改策略参数会改变经济行为，不能作为架构或文档整理的一部分。

## 单位和生效方式

金额单位为人民币元，成交数量单位为股。未注明“自然日”的窗口/计数按交易日观测推进。比例 `0.08` 表示 8%；`-0.08` 表示下跌 8%。`bps` 为基点，1 基点=0.01%，所以 10 基点=0.10%。相关系数和排名没有金额单位。

成交参与率是“同一股票当日累计成交股数 ÷ 前一已知日成交股数”，不是仓位比例。买、卖与预置保护合并计数；显式零成交量不被更早的正值替代。这是日线容量代理，不是开盘或止损价成交保证。

默认路径为 daily_rotation + ret63 + 最多两只 + ATR 风险预算。可选 sticky、迟滞、尾部和共同波动约束仍有独立参数，但不会因为写在配置中就全部生效。下表标明路径；默认关闭不代表相关逻辑已经删除。

## 1. 回测与数据域

| 参数 | 默认值 | 说明 |
|---|---|---|
| `universe` | 26 只 | 状态机、宽度与相关性计算池；存在事后强势池偏差 |
| `core_universe` | 7 只 | 实际持仓候选池 |
| `start` | `2025-04-01` | 默认回测起点 |
| `end` | `2026-08-31` | 默认回测终点 |
| `initial_capital` | 2,000,000 | 默认本金；固定参考验收按各参考的本金分别运行 |

## 2. 成本与成交

| 参数 | 默认值 | 说明 |
|---|---|---|
| `commission_bps` | 2.5 | 双边佣金 |
| `min_commission` | 5.0 元 | 每笔最低佣金 |
| `stamp_tax_bps` | 5.0 | 卖出印花税 |
| `transfer_fee_bps` | 0.1 | 双边过户费 |
| `slippage_bps` | 10.0 | 双边滑点 |
| `limit_ratio_main` | 0.099 | 主板涨跌停近似阈值 |
| `limit_ratio_gem` | 0.199 | 创业板/科创板涨跌停近似阈值 |
| `max_adv_participation` | **0.08** | 每个标的全天买卖与预置止损累计共享前一已知日量的8%。设为0时仅表示关闭成交参与率限制的配置诊断，不能代替默认验收；日量代理不构成开盘容量认证 |
| `chase_limit_pct` | 0.07 | 高开追涨保护 |
| `min_trade_value` | 50,000 元 | 开新仓等计划阶段的最小金额；不是所有风险卖出的最小金额 |

## 3. 默认选股、定仓和同步保护

| 参数 | 默认值 | 说明 |
|---|---|---|
| `rebalance_mode` | `daily_rotation` | 每日评估次日目标集 |
| `rank_factor` | `ret63` | 默认主排序因子 |
| `max_positions` | 2 | top-2 集中持仓 |
| `rotation_rank_weights` | `[1.0, 1.0]` | equal 定仓方式的位次相对权重；ATR 定仓不使用它 |
| `rotation_sizing` | `atr_risk_budget` | ATR 风险预算 |
| `rotation_risk_pct` | 0.045 | ATR 定仓系数 `weight≈risk_pct/ATR%`；不是“单笔最多亏4.5%”的损失保证 |
| `rotation_atr_floor_pct` | 0.02 | ATR% 下限 |
| `rotation_drift_tol` | 1.00 | 超配修剪容忍带 |
| `rotation_gap_frac` | 0.05 | 补仓最小目标缺口 |
| `max_single_weight` | 0.50 | 单票目标权重上限；价格变动后实际权重可能漂移 |
| `rotation_contract` | 见下 | 轮动附加经济规则 |

`rotation_contract` 默认值：

| 子键 | 值 | 生效条件 / 经济含义 |
|---|---:|---|
| `hysteresis` | False | 关闭；开启后缓冲名次变化，可能延迟换仓 |
| `hold_rank` | 3 | 迟滞研究路径：incumbent 可暂留到第3名 |
| `switch_edge_atr` | 0.50 | 迟滞研究路径：换仓优势阈值 |
| `confirm_factors` | ret50, ret80 | 迟滞研究路径确认，不参与默认主排序 |
| `tail_risk` | False | 关闭；开启后用近期尾部跌幅限制权重 |
| `tail_lookback` | 20 | 尾部风险研究路径窗口 |
| `per_name_loss_budget` | 0.06 | 尾部风险研究路径预算 |
| `common_risk` | False | 关闭；开启后按相关组合波动限制权重 |
| `common_vol_lookback` | 20 | 共同风险研究路径窗口 |
| `common_vol_cap` | 0.06 | 共同风险研究路径日波动上限 |
| `pair_stop` | True | **默认开启**；市场过热且 top-2 高相关时，为下一交易日预置独立保护 |
| `pair_stop_market_ext` | 0.14 | 前收 EWI 相对 MA25 乖离至少14%；用于触发保护的市场过热条件 |
| `pair_stop_corr20` | 0.70 | 前收时两持仓最近20日收益相关性下限；至少需要10个共同有效观测 |

保护价复用 `corr_sell_pct=-0.045`。是否“武装”只读取前一收盘可知的市场乖离、持仓和
20日相关性；下一交易日每只股票**独立**触发自己的 -4.5% 保护价。某只先触发后无需等待
另一只当天稍后是否触发，不会使用另一只股票当天稍后的最低价反推先前的成交。若开盘已跳过
保护价按开盘成交，否则按预置保护价成交，并计正常滑点与费用。

## 4. 可选 sticky 候选过滤路径

以下参数仍被 sticky 代码读取，但不属于 `daily_rotation` 的默认主选股面：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `entry_mode` | `vote_gate` | sticky 入场模式；daily_rotation 主路径绕过 |
| `momentum_blend` | ret5 .25 / ret14 .35 / ret63 .40 | sticky 合成动量 |
| `score_weights` | .55 / .25 / .20 | sticky 候选排序权重 |
| `trend_ma` | 28 | 趋势均线；用于 sticky/趋势死亡与市场宽度，不作为双持仓保护的武装阈值 |
| `fast_ma` | 10 | sticky 快均线 |
| `breakout_window` | 20 | sticky 唐奇安窗口 |
| `breakout_proximity` | 0.98 | sticky 突破接近阈值 |
| `atr_window` | 20 | ATR 窗口；默认 ATR 定仓使用 |
| `channel_atr_mult` | 1.5 | sticky 通道阈值 |
| `volume_fast` | 5 | 量比快窗 |
| `volume_slow` | 60 | 量比慢窗 |
| `volume_min_ratio` | 0.90 | 量能门槛 |
| `universe_keep` | 26 | sticky 候选保留数 |
| `vote_min` | 4 | sticky 四票门 |
| `recovery_mom_min` | 0.01 | RECOVERY 修复通道动量下限 |
| `recovery_high_gap` | 0.10 | RECOVERY 距高点容忍带 |
| `winner_base_weight` | 1.0 | sticky 倾斜基准 |
| `winner_tilt` | 2.5 | sticky 赢家倾斜 |
| `winner_tilt_factor` | `volume` | sticky 倾斜因子 |
| `winner_slot_mult` | 1.25 | sticky 固定槽位倍率 |
| `topup_cooldown_days` | 5 自然日 | sticky 回补冷却 |
| `no_topup_ext` | 0.15 | sticky 高乖离禁补 |
| `rebalance_drift_tol` | 1.40 | sticky 漂移容忍带 |
| `pyramid_boost1` | 0.20 | sticky 一级盈利门槛 |
| `pyramid_boost1_mult` | 1.3 | sticky 一级倍率 |
| `pyramid_boost2` | 0.60 | sticky 二级盈利门槛 |
| `pyramid_boost2_mult` | 1.6 | sticky 二级倍率 |

## 5. 个股退出

| 参数 | 默认值 | 说明 |
|---|---|---|
| `single_day_crash_pct` | -0.08 | 单日闪崩退出 |
| `two_day_crash_pct` | -0.12 | 两日累计闪崩退出 |
| `hard_stop_pct` | 0.12 | 相对建仓成本硬止损 |
| `chandelier_tiers` | 3.5 / 2.8 / 2.4 / 2.1 × ATR | 浮盈越高，吊灯越紧 |
| `trend_break_days` | 2 | MA28 趋势死亡确认天数 |

## 6. 市场状态机

| 参数 | 默认值 | 说明 |
|---|---|---|
| `regime_ma` | 25 | EWI 趋势均线 |
| `regime_reclaim_ma` | 10 | 危机解除均线 |
| `regime_reclaim_days` | 1 | 解除确认天数 |
| `breadth_weak` | 0.38 | 弱势宽度 |
| `breadth_strong` | 0.50 | 强势宽度 |
| `breadth_crash` | 0.10 | 极低宽度阈值；只有同时满足价格确认才进入 CRASH |
| `breadth_crash_ret5` | -0.03 | 低宽度 CRASH 的 EWI 5日价格确认；避免“宽度很低但价格强”来回横跳 |
| `ewi_ret5_crash` | -0.12 | EWI 5日危机阈值 |
| `ewi_ret5_weaken` | -0.06 | EWI 5日弱化阈值 |
| `crash_escape_ret5` | 0.06 | 危机解除动量 |
| `crash_escape_breadth` | 0.40 | 危机解除宽度 |
| `trend_through_ret20` | 0.10 | 强趋势直通 |
| `exposure_caps` | 1.0 / **0.80** / 0 / 1.0 | TREND / WEAKEN / CRASH / RECOVERY；各状态的目标敞口上限 |

## 7. 组合风险栈

| 参数 | 默认值 | 说明 |
|---|---|---|
| `corr_sell_pct` | -0.045 | 多龙头同步抛售阈值；同时复用于高相关双持仓的**独立**预置保护价 |
| `corr_liquidation_count` | 6 | 核心池单日跌幅同时越过 corr_sell_pct 的数量阈值 |
| `corr_flat_days` | 14 自然日 | 相关性清仓冷静期 |
| `peak_window` | 40 | 滚动峰值窗口 |
| `absolute_dd_floor_trigger` | -0.21 | 全期绝对回撤熔断 |
| `abs_floor_rearm` | -0.12 | 熔断重新武装线 |
| `dd_tiers` | 12%→.55 / 16%→.25 / 20%→0 | 滚动回撤梯 |
| `dd_abs_tiers` | 10.5%→.78 / 14.5%→.52 | 绝对回撤梯 |
| `dd_abs_release_on_heal` | True | 市场修复后允许解除绝对梯 |
| `deep_dd_floor` | 0.25 | 深回撤底仓上限 |
| `ramp_step` | 0.40 | 风险解除恢复步长 |
| `market_heal_bounce` | 0.08 | EWI 修复要求 |
| `heal_breadth` | 0.45 | 修复宽度要求 |
| `decay_trigger` | -0.02 | 高位两日衰减触发 |
| `decay_min_ext` | 0.08 | 衰减最低乖离 |
| `decay_cap` | 0.55 | 衰减后敞口上限 |
| `flat_cooldown_days` | 4 | 清仓冷却 |
| `vol_target_daily` | 0.0 | 全组合波动率目标化关闭 |
| `vol_target_lookback` | 20 | 波动率目标化回看窗 |
| `pulse_threshold` | -0.065 | 组合单日脉冲阈值 |
| `pulse_trim` | 0.25 | 脉冲期敞口 |
| `pulse_cooldown_days` | 3 | 脉冲冷却 |


## 相互作用与覆盖文件

JSON 覆盖文件可只给出需要修改的项；嵌套字典按键合并。例如 `{"initial_capital": 1000000.0}` 只改变本金。参数必须来自已有模式，不接受拼写错误或悄悄引入的新字段。运行结果保存最终完整配置及其哈希。

回撤梯中的 `dd` 是正的跌幅大小、`target` 是敞口比例；`chandelier_tiers` 中 `profit` 是相对建仓价的浮盈上界、`atr_mult` 是该层保护倍数。默认浮盈上界分别为8%、20%、50%和一个覆盖剩余区间的数值，保护倍数依次3.5、2.8、2.4、2.1。恢复步长0.40表示最多40个百分点，并非上涨40%。这些机制同时受市场状态、现金、取整和成交约束影响。

固定参考验收不会改写对照自身的窗口、本金或阈值。关闭参与率、修改本金或增加滑点后的结果会被标为配置或压力诊断，不会冒充默认正式验收。
