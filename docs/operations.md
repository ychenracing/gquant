# 运行指南

## 常用命令

`python -m gquant --help` 列出全部命令。`config`输出默认配置，`validate-data`校验冻结数据，`backtest`运行连续模拟账户，`validate`运行固定参考与诊断，`account-init`接管人工真实账户，`resume-account`按实际成交和显式账户事件继续状态，`forward-record`追加前向观察证据，`fetch-data`只获取独立候选数据。

```sh
python -m gquant validate-data --data-dir data
python -m gquant backtest --data-dir data --output outputs/backtest
python -m gquant validate --data-dir data --output outputs/validation --capital-scan
```

`validate`退出码0表示四个固定参考与账本全部通过；当前默认经济状态为3/4，因此正常返回1并发布完整报告。退出码2表示参数、输入、网络或发布错误。

## 接管人工账户

真实账户首次接管必须显式确认风险状态重置，因为系统没有你接管日前的真实高水位、冷却计数和历史风险状态。账户文件示例：

```json
{
  "cash": 500000.0,
  "positions": [
    {
      "symbol": "sz300308",
      "shares": 1000,
      "sellable_shares": 1000,
      "entry_price": 150.0,
      "entry_date": "2026-01-05"
    }
  ]
}
```

```sh
python -m gquant account-init \
  --account account.json \
  --as-of 2026-08-28 \
  --risk-reset \
  --data-dir data \
  --output outputs/account
```

`--risk-reset`不是交易指令，而是证据边界：接管日之前的真实账户风险历史未知，从该日开始重新建立组合高水位和冷却状态。输出包含`state.json`、`account.json`、`next_orders.json`、`conditional_orders.json`、`reconciliations.json`、配置和身份。`state.json`同时封存截至接管日的OHLCV历史前缀身份；后续可以追加未来交易日，但已处理历史被修订时续接会拒绝运行。`next_orders.json`是下一交易日普通计划；`conditional_orders.json`是前一收盘已决定、只有次日触及预置条件后才允许出现的保护成交。两者都只是人工操作依据，不会发送给券商。

## 用实际成交继续账户

券商实际成交和账户事件通过显式JSON输入。一个交易日即使完全没有成交，也应提供空`fills`数组，表示该会话的实际回报是权威的“零成交”。每笔实际成交必须显式提供`fees`；若真实费用为零也写`"fees": 0`，系统不会把缺失费用静默当作零。普通成交必须匹配普通计划，或匹配前一收盘已发布的条件保护；否则拒绝。人工主动偏离策略计划时使用`manual_adjustments`单独记录，不能把它伪装成策略原计划。`cash_flows`只表示外部入金/出金，不计入策略收益或回撤。未知顶层字段、未知会话字段和非法事件结构全部失败关闭。

```json
{
  "sessions": [
    {
      "date": "2026-08-31",
      "fills": [
        {
          "symbol": "sz300308",
          "side": "sell",
          "shares": 600,
          "price": 150.5,
          "fees": 10.0
        }
      ],
      "manual_adjustments": [],
      "cash_flows": [
        {"kind": "deposit", "amount": 10000.0, "note": "capital added"}
      ],
      "corporate_actions": []
    }
  ]
}
```

支持的公司行动需显式给出：

```json
{"symbol":"sz300308","kind":"split","ratio":2.0}
{"symbol":"sz300308","kind":"cash_dividend","cash_per_share_net":0.5}
```

拆送会同步调整真实股数、持仓成本、待执行普通订单和已武装条件保护。若现金分红发生时该股票仍有已武装条件保护，因为研究行情使用供应商复权价格，事件还必须提供券商/交易所口径确认的`reference_price_adjustment`，用于同步保护参考价；系统不会猜测这个调整值。没有条件保护时，现金分红只按`cash_per_share_net`增加真实现金。

继续运行：

```sh
python -m gquant resume-account \
  --state outputs/account \
  --actual-events actual.json \
  --end 2026-08-31 \
  --data-dir data \
  --output outputs/account-next
```

实际成交会更新真实现金和库存，并记录授权来源与数量偏差；未完成的普通完整退出会继续保留为待办。条件保护只授权真实成交，不使用日线最低价替代券商回报。人工偏差单独标记为`manual_override`。入金/出金会改变真实账户权益和后续可用资金，但绩效曲线、组合回撤高水位、波动率目标和单日脉冲使用剔除外部现金流后的连续绩效权益，因此入金不算收益、出金不算亏损。续接前会核对保存日及以前的已准入行情前缀，历史数据发生修订时直接失败；单纯增加后续交易日不会破坏续接。实际回报不会被伪装成模拟成交。系统仍不连接券商、不提交订单。

## 前向观察日志

人工账户每次完整发布后，可执行：

```sh
python -m gquant forward-record \
  --state outputs/account-next \
  --output outputs/forward
```

该命令先读取并校验账户代次，再把当日`state.json`哈希、身份、实际账户、普通计划、条件保护和当日核对结果写入独立事务代次。相同日期、相同状态重复执行不会增加记录；相同日期但状态不同，或日期倒退，都会拒绝。日志只追加已经形成的证据，不会回写策略、账户或冻结行情。它用于后续判断建议与真实执行偏差，不会把历史诊断重新包装成样本外。

## 模拟账户连续区间

`backtest --interval START END`只从完整模拟账户轨迹截取区间，保留此前现金、持仓和风险状态。`EngineState`还支持同一模拟账户在交易日收盘保存并恢复；测试要求“保存→JSON往返→恢复”与一次性完整回放的逐日权益、敞口、状态、事件和成交完全一致。

## 原子输出与恢复

`latest.json`指向`runs/<标识>/`的一次完整发布，`manifest.json`固定全部文件哈希。写入过程由`.publish.lock`互斥，只有完整代次写好后才原子更新指针。提交点前失败不会移动`latest.json`；提交点后若清理失败，应先用`gquant.infrastructure.artifacts.read_latest()`验证指针和文件，不要仅凭进程退出码决定是否重跑。

数据获取同样先写独立快照并完整校验，再发布；不会逐个覆盖当前冻结CSV形成混合快照。冻结研究数据不会因`fetch-data`自动替换。

## 常见错误

| 提示 | 含义/处理 |
|---|---|
| snapshot hash mismatch | 数据字节与清单不一致；恢复完整正确快照，不改哈希掩盖变更 |
| duplicate/invalid snapshot date | 日期重复、为空或不合法；拒绝静默去重 |
| requested start precedes snapshot | 请求起点早于快照；补齐历史或缩短请求，不静默截短 |
| requested end exceeds snapshot | 请求终点晚于快照；补齐数据或缩短请求，不静默截短 |
| unknown configuration field | 参数名拼写或配置面不合法 |
| initial account takeover requires explicit --risk-reset | 首次人工账户接管缺少明确风险重置边界 |
| admitted data history differs from saved account state | 保存日及以前的复权行情已变化；先核对供应商修订/公司行动并建立新的明确接续边界，不能删除哈希或静默重算旧账户 |
| unknown actual session field | 实际事件出现未支持字段；修正输入或明确扩展事件合同，不静默忽略 |
| conflicting forward observation | 同一日期已经保存了不同状态；先核对来源，不覆盖已有前向记录 |
| publication writer is active | 同一输出目录已有写入者；先核实任务和锁状态 |

报告目录默认不入Git。需要保留证据时保存整个代次、源码身份、环境、配置和输入身份，而不是只复制收益数字。
