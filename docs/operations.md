# 运行指南

## 常用命令

`python -m gquant --help` 列出全部命令。`config`输出默认配置，`validate-data`校验冻结数据，`backtest`运行连续模拟账户，`validate`运行固定参考与诊断，`account-init`接管人工真实账户，`resume-account`按实际成交继续账户状态，`fetch-data`只获取独立候选数据。

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

`--risk-reset`不是交易指令，而是证据边界：接管日之前的真实账户风险历史未知，从该日开始重新建立组合高水位和冷却状态。输出包含`state.json`、`account.json`、`next_orders.json`、`reconciliations.json`、配置和身份。`next_orders.json`只是下一交易日的人工决策清单，不会发送给券商。

## 用实际成交继续账户

券商实际成交和公司行动通过显式JSON输入。一个交易日即使完全没有成交，也应提供空`fills`数组，表示该会话的实际回报是权威的“零成交”。

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

继续运行：

```sh
python -m gquant resume-account \
  --state outputs/account \
  --actual-events actual.json \
  --end 2026-08-31 \
  --data-dir data \
  --output outputs/account-next
```

实际成交会更新真实现金和库存，并记录与计划数量的偏差；未完成的完整退出会继续保留为待办。实际回报不会被伪装成模拟成交。系统仍不连接券商、不提交订单。

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
| requested end exceeds snapshot | 请求窗口晚于快照；补齐数据或缩短请求，不静默截短 |
| unknown configuration field | 参数名拼写或配置面不合法 |
| initial account takeover requires explicit --risk-reset | 首次人工账户接管缺少明确风险重置边界 |
| publication writer is active | 同一输出目录已有写入者；先核实任务和锁状态 |

报告目录默认不入Git。需要保留证据时保存整个代次、源码身份、环境、配置和输入身份，而不是只复制收益数字。
