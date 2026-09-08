# Gquant

Gquant 是面向 A 股科技产业链的日线量化研究与人工决策支持工具。它读取经过校验的行情，在收盘后评价市场状态、选择股票并形成下一可交易日的模拟订单，输出权益、成交和风险事件报告。它不连接券商，不替代真实账户核对，也不自动下单。

默认策略在 7 只核心股票中按最近 63 个交易日收益率排序，最多持有 2 只，结合平均真实波幅（ATR）定仓、市场状态与组合回撤控制。优势是规则可复算、选股集中且执行约束明确；代价是对固定股池和趋势行情依赖较强。当前固定参考为3/4：三项通过，glmcsm仅因收盘最大回撤超过固定门槛失败；执行账本保持正确。该结果不代表跨行情稳定盈利。

## 开始使用

需要 Python 3.12 或 3.13。所有命令在仓库根目录运行，先创建虚拟环境：

```sh
python -m venv .venv
```

Linux/macOS 激活：`source .venv/bin/activate`。Windows PowerShell 激活：`.venv\Scripts\Activate.ps1`；也可直接调用 `.venv\Scripts\python.exe`，不必修改系统执行策略。

```sh
python -m pip install -r requirements-runtime.txt
python -m pip install --no-deps --no-build-isolation -e .
python -m gquant validate-data --data-dir data
python -m gquant backtest --data-dir data --output outputs/backtest
```

构建工具由 `requirements-runtime.txt` 安装；研究与测试工具见[开发指南](docs/development.md)。安装后也可直接运行 `gquant` 命令。安装包只包含程序，行情和证据保留在仓库，因此从其他目录运行时须给出 `--data-dir` 的实际路径。

默认回测窗口是 2025-04-01～2026-08-31，初始资金为 200 万元；它不会自动延长到今天。运行 `python -m gquant config` 查看完整默认值。参数覆盖文件只需包含要覆盖的字段；未知字段、错误类型或非有限数字会报错。

```sh
python -m gquant backtest --data-dir data --output outputs/interval --interval 2026-06-20 2026-08-30
python -m gquant validate --data-dir data --output outputs/validation --capital-scan
```

`--interval`仅从完整模拟账户轨迹截取区间，保留此前持仓、现金和风险状态。当前固定参考为3/4，所以`validate`会完整发布结果并返回退出码1；这不是工程故障。

真实人工账户可在明确风险重置边界后接管，并在之后输入券商实际成交继续状态：

```sh
python -m gquant account-init --account account.json --as-of 2026-08-28 --risk-reset --data-dir data --output outputs/account
python -m gquant resume-account --state outputs/account --actual-events actual.json --end 2026-08-31 --data-dir data --output outputs/account-next
```

这两个命令只保存/核对状态并生成下一交易日人工订单清单，不连接券商、不自动下单。格式与恢复规则见[运行指南](docs/operations.md)。

## 如何看结果

输出目录中的 `latest.json` 指向一次完整运行。程序先写入 `runs/<随机标识>/` 的全部文件和哈希，再原子更新指针；指针只会引用完整结果。提交指针前失败会保留上一份结果；提交后若进程中断或清理失败，应按[运行指南](docs/operations.md)核对指针，而不能仅凭退出码判断是否发布。回测输出包括 `report.md`、`report.json`、`daily.csv`、`fills.csv`、`config.json` 和 `identity.json`。不要把目录里一个孤立文件当作验收结果。

收益和回撤以比例保存：`0.10` 代表 10%，`-0.10` 代表 −10%。配置中的 `bps` 表示基点，1 基点是 0.01%。例如默认单边滑点 10 基点，即买入价上浮 0.10%、卖出价下调 0.10%，另计手续费。

默认成交参与率上限 `max_adv_participation=0.08` 表示：**单只股票当日所有模拟买入、卖出和预置止损的累计股数，最多为前一已知交易日成交股数的 8%**。例如前一日成交 100 万股，当日共享最多 8 万股额度。这不是仓位比例，也不是多日平均量，更不是集合竞价或止损价一定能成交的保证。

## 阅读入口

| 需求 | 文档 |
|---|---|
| 理解模块、依赖和数据流 | [架构与代码结构](docs/architecture.md) |
| 理解选股、定仓、市场状态和退出 | [策略说明](docs/strategy.md) |
| 查参数值、单位和生效路径 | [参数手册](docs/parameters.md) |
| 理解行情来源、数量单位和校验 | [数据合同](docs/data.md) |
| 日常命令、输出、错误与恢复 | [运行指南](docs/operations.md) |
| 理解验证标准、CI 和证据身份 | [验证说明](docs/validation.md) |
| 查历史表现、适用环境和风险 | [结果与局限](docs/results.md) |
| 参与开发、测试和构建 | [开发指南](docs/development.md) |
| 研究优先级与明确非目标 | [研究方向](docs/direction.md) |

原始证据与使用边界见 [evidence/README.md](evidence/README.md)。工程协作约定见 [AGENTS.md](AGENTS.md)。
