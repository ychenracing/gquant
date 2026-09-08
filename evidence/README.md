# 原始证据与经济轨迹

`raw/`保存不重新计算、不改写的研究文件，`index.json`固定每个文件的SHA256。这里既有历史失败和关闭成交参与率的诊断，也保存执行正确性修复前的完整经济轨迹；它们都不是当前默认结果。

当前经济行为由两类active fixtures锁定：

- `tests/fixtures/economic-oracle.json`：6组完整账户、成交和事件指纹；
- `tests/fixtures/economic-sequence-digests.json`：24组逐日权益/敞口/市场状态、全部成交和事件的完整路径指纹，覆盖默认窗口、历史诊断、参考条件、六本金及更高滑点。

两份active fixtures绑定`src/gquant`行为哈希、默认配置和冻结数据哈希，不依赖尚未产生的未来Git提交号。修复前的对应文件已原样保存为`raw/pre-correctness-economic-oracle.json`和`raw/pre-correctness-economic-sequences.json`，不能追认为当前通过证据。

当前结果由：

```sh
python -m gquant validate --data-dir data --output outputs/validation --capital-scan
```

生成。当前固定参考为3/4，因此命令返回1并完整发布报告。先核对`identity.json`与`validation.json`，再读取执行账本、参考门和诊断；不要从历史文件名中的`final`或`scorecard`推断当前状态。

`reference-inputs.json`保存四个参考的来源、窗口、本金和指标；独立成交量参考位于`tests/fixtures/volume-reference/`。维护说明见[结果与局限](../docs/results.md)和[验证说明](../docs/validation.md)。
