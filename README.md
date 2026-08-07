# AGENTS V3

一个预算有上限、状态可恢复、评测可审计的 `AGENTS.md` 实验工程。V3 的核心变化不是继续扩写提示词，而是把能够确定执行的约束移到代码、Schema、SQLite 账本和外层沙箱中。

本仓库当前提供一条完整的**离线、无网络、无真实凭据**纵向演示路径，以及用于真实运行的协议和扩展边界。示例配置故意不能通过 final-test 运行时能力门：真实 final test 必须由受信任的外层 launcher 生成并验证运行时探针报告，不能通过修改一行提示词绕过。

## 已实现的保证

- 单一 authoritative `run_config`；缺字段、未知字段、过期 deadline、空预算或错误审批哈希均 fail closed。
- SQLite 单写者事件账本，带单调序号、哈希链、任务 lease、心跳、幂等恢复和原子状态迁移。
- token、现金、供应商额度、wall-clock、tool-call 五维最坏情况预留；并发请求先预留再发出。
- 按阶段限额、软水位、closeout reserve、最大 dev 轮数、低增益停止和 `best-so-far`。
- 样本与 qualified maintainer review event 对齐；行政关闭不伪装成代码质量标签。
- 统一 knowledge cutoff、来源 checksum、近重复分组和跨 split 泄漏检查。
- 三臂逐样本配对历史-PR代理评测；cluster bootstrap、exact McNemar、Holm 双基线校正、类别功效门和 `INCONCLUSIVE`。
- 独立三臂 agent-in-the-loop 配对实验合同，用于检验“提升真实贡献质量”的构念。
- 静态 capability policy 与实际 runtime probe 分离；角色、网络、挂载、secret、socket、device 和资源上限均可验证。
- final-test profile 冻结、期望结果矩阵、结果完整性、同 profile 的中断恢复，以及 scorer 派生终态。
- final-ready bundle 对 run ID、配置/profile/runtime/capability/依赖锁哈希、cutoff、模型、限额、统计参数、时间顺序和 runner 能力做交叉绑定；SQLite 冻结入口独立复核并写入 readiness receipt。
- 所有公开库入口先执行发布 JSON Schema，再执行语义约束；不能靠绕过 CLI 注入宽松对象。
- 默认禁用不可信代码；可选 runner 要求 rootless、无网络、只读根、无 secrets、限 CPU/内存/PID/磁盘/输出/时间。

## 快速开始

要求 Python 3.11+。唯一运行依赖为 PyYAML；推荐在隔离环境中安装锁定版本。

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps

agents-v3 validate
agents-v3 demo --output work/demo-run
agents-v3 status --state work/demo-run/state.sqlite3
python -m unittest discover -s tests -v
```

`validate` 检查所有发布的 YAML/JSON 合同，但会如实报告示例 runtime report 的负向探针尚未通过。`demo` 使用确定性 stub 和合成事件样本，不访问 GitHub、不调用模型供应商、不执行 PR 代码，也不会宣称真实 final test 成功。

可复现发行包使用固定构建依赖和 `SOURCE_DATE_EPOCH`，并生成源码 ZIP、wheel、release manifest 与 `SHA256SUMS`：

```bash
python -m pip install -r build-requirements.lock
SOURCE_DATE_EPOCH=1786060800 \
  python3 tools/build_release.py --output ../agents-v3-release
```

## 工程结构

```text
AGENTS.md                         短小、不可变的语义工作者章程
config/                           authoritative 配置和故意未通过的探针样例
protocol/                         数据、评测、安全、生命周期、复现协议
schemas/                          可机器验证的 JSON Schema
src/agents_v3/                    控制器、预算、账本、评测、冻结门和 CLI
sandbox/                          rootless 不可信代码 runner 与预检
tests/                            正向、负向、恢复和端到端测试
docs/                             架构、运维、终态和 P0/P1 追踪
```

## 运行顺序

1. `Protocol Gate`：冻结目标、样本事件、指标、统计、knowledge cutoff 和终态语义。
2. `Cheap Census`：只抓低成本元数据，生成 eligibility manifest 和功效估算。
3. `Minimal Safe Vertical Slice`：用分层小样本打通三臂、评分、遥测和报告。
4. `Run Manifest Gate`：结合用户硬上限和探针实测，填写非空预算、阶段 deadline 和 closeout reserve，并审批 canonical hash。
5. `Controller`：所有任务必须经原子预算准入、lease 和工件 gate。
6. `Scale Gate`：protocol、security、sample、cost、reproducibility 全部通过后才放大。
7. `Freeze/Test`：冻结候选和完整 profile；有效失败终止，基础设施中断只补同 profile 的缺失单元。

## final test 的硬边界

- `VALID_TEST_FAILED` 不返回 dev DAG。
- `INFRA_INCOMPLETE` 只能恢复同一 profile 中尚未形成 canonical 结果的工作。
- `INCONCLUSIVE` 是功效或有效样本不足的诚实结论，不强判成功或失败。
- 终态由 scorer 根据冻结标准派生；调用者不能提交自报 `PASS`。
- 只有通过完整跨工件绑定的 bundle 才能写入冻结账本；profile 自哈希或任一引用哈希被重算也不能掩盖不一致。
- 示例 runtime report 的探针均为 `false`，因此默认工程不能误入 final test。

## 真实运行需要外部提供什么

本工程不内置 Nexus-Editor 的凭据、受保护标签或供应商密钥，也不把通用宿主机当安全边界。真实运行需要另行提供：

- 只读、限域的 GitHub collector 代理；
- 分角色的进程身份、最小挂载与网络策略；
- 受保护的 train/dev/test truth vault；
- 固定 provider/model 的外部适配器和全局队列；
- 可验证的 rootless container/VM launcher；
- 用户审批后的预算 manifest 与运行时 capability report。

未提供这些条件时，正确行为是输出 `PROTOCOL_INVALID`、`BLOCKED` 或未测试的 anytime report，而不是降级到不安全执行。

## 文档入口

- [架构](docs/architecture.md)
- [操作手册](docs/operations.md)
- [终态语义](docs/terminal_states.md)
- [评审整改追踪](docs/traceability.md)
- [安全协议](protocol/security.md)
- [评测协议](protocol/evaluation.md)
