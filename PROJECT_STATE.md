# 项目入口

Gquant提供冻结日线研究、连续账户回放、可恢复账户状态和人工决策清单，不连接券商、不自动下单。

恢复工作先读`AGENTS.md`，再按范围读取`README.md`、`docs/architecture.md`、`docs/strategy.md`、`docs/parameters.md`、`docs/operations.md`和`docs/validation.md`。代码在`src/gquant/`，冻结数据在`data/`，测试在`tests/`，保留证据在`evidence/`。

当前固定参考的真实经济状态为3/4：turtle、track、momentum通过；glmcsm仅因14.6421%的收盘最大回撤高于14.32%门槛失败。`gquant validate`因此返回1；CI必须准确复现该失败并同时证明执行账本正确，不能放宽门槛。

人工真实账户首次用`account-init --risk-reset`建立明确风险重置边界；之后用`resume-account`输入权威实际成交、人工偏差、外部现金流和显式公司行动继续状态。普通计划与前一收盘已武装的条件保护分别发布；外部现金流不进入策略收益/回撤。`forward-record`可把完整人工账户代次追加到独立前向观察日志。固定稳健性矩阵包含11项诊断并输出最差回撤与换手归因；这些诊断不改变默认经济行为。分支、提交、PR、检查和运行状态始终以GitHub实时信息为准。
