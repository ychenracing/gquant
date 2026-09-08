# 开发指南

## 环境与日常检查

```sh
python -m venv .venv
python -m pip install -r requirements-dev.txt
python -m pip install --no-deps --no-build-isolation -e .
python -m ruff check .
python -m ruff format --check .
python -m mypy
python scripts/check_repository.py
python -m pytest -q
```

先按README激活虚拟环境。运行测试无需网络；联网仅用于安装依赖、依赖漏洞数据库审计和显式fetch-data命令。冻结数值环境为numpy2.2.6/pandas2.2.3，CI覆盖Python3.12/3.13；不得用另一环境的成功结果冒充固定环境结果。

```sh
python -m pytest -q --cov=gquant --cov-branch --cov-report=term-missing --cov-report=xml
python -m bandit -r src/gquant
python scripts/verify_distribution.py
python -m gquant validate --data-dir data --output outputs/acceptance --capital-scan
```

coverage统计整个程序包，语句与分支合计覆盖率最低85%。可复现构建检查生成两份wheel并比较哈希，核对包内源码范围，再把wheel安装到隔离目录，在仓库外运行命令，防止“源码目录可跑但安装后不可用”。wheel不携带CSV、测试或原始研究证据。

## 改动原则

先理解入口和受影响调用链，优先复用现有模块和标准库。业务对象、执行规则和研究口径各有归属，不在脚本中重写公式。错误输入直接失败，不用默认值掩盖未知字段，不把未成交意图当成真实持仓变化。

针对一个问题先跑受影响测试，再在稳定候选运行完整适用门槛。经济轨迹的输入和预期值是合同，不允许为了重构通过而刷新它们。需要改变策略经济行为时应明确列出候选、基准、成本和验收范围。

新增模块必须遵循架构依赖；复杂函数应按有实际职责的边界拆分，不为满足行数新增无意义转发层。评审完整任务diff，关注配置值、数据字节、算法顺序、并发发布、异常处理和文档命令是否一致。

CI将工程和经济工作分开：工程失败、经济门失败、压力诊断失败分别报告。验证工作流不写回仓库、不拉取实时行情，也不包含自动交易或跨仓库调度。
