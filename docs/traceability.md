# V2 二次评审到 V3 的可追踪整改

本表把已批准的 P0/P1 结论映射到机器合同、执行代码和测试。`协议` 表示定义语义，`执行` 表示由控制器或外层 runner 强制；两者不能互相替代。

| 评审项 | V3 落点 | 验证方式 |
|---|---|---|
| P0-1 final test 矛盾 | `protocol/evaluation.md`、`final_test.py`、`state_machine.py` | valid failure 单向终止；仅同 profile 的缺失单元可恢复 |
| P0-2 构念错位 | `protocol/agent_in_loop.md`、`agent_loop.py` | 历史 PR 明示为代理；三臂同 issue/base/model/budget 配对合同 |
| P0-3 标签/快照错位 | `sample.schema.json`、`dataset.py` | qualified review event、event SHA、身份依据、cohort 与 exclusion reason 校验 |
| P0-4 C 误作上限 | `protocol/evaluation.md`、`evaluation.py` | stability 独立报告，不改变准确率门槛 |
| P0-5 显著性/precision | `evaluation_profile.schema.json`、`evaluation.py`、`power.py` | 效应量与 CI 同时过门；配对检验、Holm、群组 bootstrap、类别功效门 |
| P0-6 时间泄漏 | `knowledge_manifest.schema.json`、`knowledge.py` | source time/checksum/commit、cutoff、近重复 union、group split 和污染披露 |
| P0-7 truth 隔离 | `protocol/security.md`、`security.py`、`runtime.py` | 角色 ACL、受保护 mount class、无网络/secret/socket 负向探针 |
| P0-8 空预算/软着陆 | `run_config.schema.json`、`config.py`、`budget.py`、`scheduler.py` | 非空五维 cap、调用前原子预留、stage cap、soft waterline、closeout reserve |
| P0-9 harness 安全 | `capability_manifest.schema.json`、`runtime_capability_report.schema.json`、`readiness.py`、`sandbox/` | 静态策略 + 实际探针；launcher HMAC/nonce；source-id 绑定；rootless/no-network/read-only/resource-limit 预检 |
| P1-1 anytime | `reporting.py`、CLI demo | 始终可生成 best-so-far 与未测试说明 |
| P1-2 边际递减 | `config.py`、`store.py`、`scheduler.py` | max iterations、low-gain patience、cost-per-gain 与 best 候选 |
| P1-3 状态可信/恢复 | `store.py`、`scheduler.py`、`reporting.py` | 单写者、event sequence/hash chain、lease、heartbeat、幂等 key、staleness/ETA |
| P1-4 状态机闭合 | `state_machine.py`、`scheduler.py` | READY→RUNNING 原子 claim、retry/split/superseded/blocked 返回路径及 stale recovery |
| P1-5 并发/evaluator 成本 | `run_config`、`scheduler.py`、`artifacts.py` | 总槽位、evaluator 占槽、确定性 gate 优先、语义 evaluator 与 benchmark evaluator 分权 |
| P1-6 provider/复现 | `telemetry.py`、`providers.py`、`profiles.py`、`artifact_resolution.schema.json` | 错误分类/request ID/重试轨迹；evaluation 禁 failover；完整 profile hash 与实际工件 bytes 解析 |
| P1-7 文件包装/规模 | 根 `AGENTS.md` + `config/` + `schemas/` + `protocol/` + `src/` | 无外层代码围栏；短章程与可执行控制平面分离 |

## 不能由本仓库单独证明的事项

以下属性依赖部署环境，因此 V3 选择 fail closed 而不是写成自我声明：真实 OS 身份隔离、网络 egress enforcement、truth vault ACL、供应商账户额度、容器镜像内容与 digest、GitHub 代理的只读性。它们必须由外部 launcher/proxy 生成运行时证据；未通过时 final test 不启动。
