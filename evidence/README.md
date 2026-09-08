# 原始证据

`raw/` 保存未重新计算或改写的研究文件；`index.json` 固定每个文件的SHA256。它们包括失败结果和关闭成交参与率限制后的诊断，不是当前默认配置的验收报告。文件中的源提交、运行标识和参数属于证据身份，不是可调整的文档文案。

当前结果由 `python -m gquant validate --data-dir data --output outputs/validation --capital-scan` 生成；阅读其完整代次与identity，不根据原始文件名中的final或scorecard推断通过状态。

`reference-inputs.json` 保存四个参考的来源、窗口、本金与指标；`tests/fixtures/economic-oracle.json` 固定连续账户/成交/事件的回归轨迹；独立成交量参考位于 `tests/fixtures/volume-reference/`。固定环境的完整账户序列位于 `../tests/fixtures/economic_sequences.json`，覆盖24组不重复的资金、成本与窗口条件。各类证据用途不同，不能互相替代。维护文档见 [结果与局限](../docs/results.md) 和 [验证说明](../docs/validation.md)。
