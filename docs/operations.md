# Operations runbook

本手册区分 `demo`、`probe/development` 和 `final`。任何阶段都不得把 demo 的 stub、示例哈希或未通过的运行时探针解释为真实实验结果。

## 1. 离线自检

```bash
agents-v3 validate
python -m unittest discover -s tests -v
sh -n sandbox/run-untrusted.sh
RUNNER_PREFLIGHT_ONLY=1 \
RUNNER_COMMAND_ALLOWLIST=/usr/local/bin/npm \
RUNNER_WALL_SECONDS=60 \
RUNNER_DISK_MB=64 \
RUNNER_OUTPUT_MB=8 \
sandbox/run-untrusted.sh \
  agents-v3-runner@sha256:REPLACE_WITH_64_LOWERCASE_HEX \
  /absolute/minimal/source \
  /absolute/dedicated/output \
  /usr/local/bin/npm test
```

最后一条预检必须在 engine、镜像 digest、目录关系或资源参数不满足时失败；它不会回退到宿主机执行。

## 2. 纵向演示

```bash
agents-v3 demo --output work/demo-run
agents-v3 status --state work/demo-run/state.sqlite3
```

演示应留下：SQLite 状态库、`STATUS.md`、`ANYTIME_REPORT.md`、合成 eligibility/knowledge/agent-loop 工件和校验摘要。演示终点位于 final-test barrier 之前，因为示例 runtime report 故意未通过。

## 3. 创建真实 manifest

1. 复制 `config/run_config.example.yaml`，先设置 `approval.confirmed: false`。
2. 将 `mode` 改为 `probe`、`development` 或 `final`。
3. 使用用户硬上限与纵向探针 P95 填写所有五维预算、阶段 cap、软水位和 reserve。
4. 配置固定 provider/model；evaluation 禁止自动 failover。
5. 设定真实 deadline、knowledge cutoff 和统计功效目标。
6. 校验 canonical payload，交由用户确认后写入 approval 身份、时间和 SHA-256。
7. 任何审批后的字节变化都使旧审批失效。

## 4. 运行时能力证明

静态 `capability_manifest` 只描述允许什么，不证明实际发生了隔离。受信任 launcher 必须从真实进程/容器生成 `runtime_capability_report`，并逐角色证明：

- collector 仅能对 allowlist 主机执行 GET；
- candidate/judge 无 GitHub 网络、truth mount、host home、runtime socket 和 secrets；
- dev evaluator 只读 dev truth；final evaluator 只读 test truth；
- untrusted runner 无网络、无 credentials、无宿主危险挂载；
- 路径逃逸、GitHub 写入和权限提升负向探针确实失败；
- CPU、内存、PID、磁盘、输出和 wall-clock 限制由外层实施。

报告哈希和静态 capability manifest 哈希都必须进入 evaluation profile。报告中的 `unauthorized_truth_paths_absent` 表示“除角色明确获准的 truth class 外均不可见”，不是要求 evaluator 看不到其授权标签。手工把 `all_required_probes_passed` 改为 `true` 不会替代逐项校验。

`validate --require-final-ready` 会同时校验配置、冻结 profile、runtime report、静态 capability manifest、依赖锁和 profile 中全部非派生工件；它检查跨工件的一致性与内容寻址。必须通过 `--launcher-key-file` 提供受保护 launcher 的原始 HMAC key，并用 `--artifact-resolution-manifest` 把每个 profile hash 指向 manifest 同目录树内的真实文件。key 文件和工件路径均拒绝 symlink。运行时报告必须通过受保护 launcher 边界写入，不能把报告自哈希误认为进程身份认证。

## 5. 冻结与恢复

冻结前确认 protocol、schema、candidate、annex、dataset、rubric、prompt、packer、retriever、provider、model、sampling、harness tree、dependency lock、sandbox digest 和 capability report 均有有效哈希；所有工件哈希必须能从实际只读 bytes 重算。

生产冻结必须调用 SQLite Store 的 bundle-aware 冻结入口；该入口再次运行 final-ready 校验，并把 readiness receipt 哈希写入账本。不得把低层 `FinalTestBarrier` 类当作生产授权入口。

冻结后：

- candidate-side 任务被拒绝；
- 每个 `(sample_id, arm, repeat)` 只有一个 canonical 结果；
- 已完成 canonical 单元不可覆盖；
- transport/429/5xx/timeout 等中断可按冻结 retry policy 补齐；
- schema/semantic/protocol 错误不得伪装成基础设施重试；
- 有效评分结束后，由 scorer 原子写入终态。

## 6. 收尾

进入软水位后停止新探索，保留 `best-so-far`。closeout 只允许：持久化已完成事件、释放或结算在途预留、冻结最佳候选、生成状态/anytime report，并在预留足够且所有门已通过时执行一次 final test。

交付报告必须明确：候选 hash、是否执行 valid final test、终态、有效样本/群组/类别计数、两条 baseline 的配对结果、预算已耗/在途/剩余、错误分类、profile hash 和污染风险。

## 7. 可复现发行

构建环境先安装 `build-requirements.lock`。发行命令在临时干净源码副本中构建 wheel，因此不会把 `build/`、`*.egg-info` 或缓存写回 canonical tree；源码 ZIP 的文件顺序、时间戳与权限也被规范化。

```bash
SOURCE_DATE_EPOCH=1786060800 \
  python3 tools/build_release.py --output /absolute/new/release-directory
sha256sum -c /absolute/new/release-directory/SHA256SUMS
```

输出目录必须为空。对同一源码、同一锁文件和同一 `SOURCE_DATE_EPOCH` 连续构建，应得到相同的源码 ZIP 与 wheel 哈希。
