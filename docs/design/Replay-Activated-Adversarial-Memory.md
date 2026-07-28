# R³E 持续对抗记忆系统升级路线计划书

> 文档定位：R³E 后续阶段系统升级路线
> 核心主题：面向红蓝持续对抗的累积式、可验证、非 Prompt 化记忆系统
> 当前阶段：系统架构设计与工程实施规划
> 目标版本：R³E Continual Adversarial Memory v1
> 建议代号：Replay-Activated Adversarial Memory（RAAM，重放激活的对抗记忆）

---

## 工程实施状态（2026-07-28）

本设计已进入系统升级阶段，当前不是实验结果阶段。

- [x] Phase 0：继承并保持 Whole-Policy System Upgrade Complete V1 权威边界；
- [x] Phase 1：`VerifiedEpisode` 与不可变、hash-chain 经验档案；
- [x] Phase 2：`ControlMemory`、白名单、版本与 append-only lifecycle；
- [x] Phase 3：仅使用 runtime observable 的 `FailureDescriptor`；
- [x] Phase 4：active-bank-only retrieval、abstention、compatibility、conflict
  和 current-policy reactivation；
- [x] Phase 5：非 Prompt `ExecutionPlan` compiler，`memory_token_cost=0`；
- [x] Phase 6：确定性 paired shadow runner 与五类资格门基础实现；
- [x] Phase 7：Active Memory Bank schema/store/hash，以及 bank 变化绑定
  `PolicyState` child 的 promotion candidate；
- [x] Phase 8：policy compatibility、显式继承、增量 revalidation、关系图、
  merge/split、bank compression、跨 store audit 与跨轮保留；
- [x] Phase 9：只暴露公开摘要的 memory-aware red capability packet，以及
  policy+bank 双绑定的 bypass/deepening/conflict operator 和真实 RTL source
  hash 后置校验。

协议说明见
`docs/protocols/replay-activated-adversarial-memory-v1.md`，逐项工程状态见
`docs/checklists/raam-implementation-v1.md`。当前实现只声明确定性系统基础设施，
不声明真实模型驱动的跨轮记忆增益。RAAM System Upgrade Complete V1
里程碑只冻结协议、接口和 deterministic fake 验证。

## 1. 背景与问题定义

### 1.1 R³E 当前研究主线

R³E 的核心并不是在固定 RTL bug 测试集上重复执行一次性修复，而是构建一个持续运行的红蓝对抗系统：

1. 当前蓝方修复策略 \(B_t\) 面对红方挑战；
2. 红方针对当前蓝方尚未掌握的能力区域生成可执行功能缺陷；
3. 蓝方尝试修复，并形成完整、可验证的失败或成功轨迹；
4. 系统根据这些轨迹修订蓝方状态；
5. 冻结重放验证蓝方是否真正吸收了此前未掌握的失败；
6. 通过验证后的新蓝方继续接受红方的新一轮挑战。

该循环的终极目标是：

> 让蓝方在不断变化的红方攻击分布下，持续积累可复用能力，而不是仅对一个固定 benchmark 进行一次性适配。

### 1.2 静态测试集的局限

固定 RTL bug benchmark 存在以下结构性限制：

- bug 类型、设计规模和错误分布有限；
- 蓝方可以逐渐适配固定样本，而不一定获得真正可迁移能力；
- 无法持续暴露当前蓝方的最新弱点；
- 很难研究能力积累、遗忘、冲突和长期演化；
- 重复提升可能来自任务分布变化，而非蓝方状态本身的增强。

R³E 的红方能够根据当前蓝方状态动态产生新缺陷，因此有机会把修复系统从“静态 benchmark 优化”推进到“持续攻防中的能力形成”。

### 1.3 旧式记忆方案的问题

旧式记忆通常采用以下路径：

```text
历史修复轨迹
→ 蒸馏成自然语言经验
→ 检索相关经验
→ 拼接到 Prompt
→ LLM 根据经验生成补丁
```

该方案存在四个问题：

1. **主体错位**：记忆属于蓝方 agent/system，但实际却被实现成 LLM Prompt 的附加文本。
2. **Token 成本增加**：记忆越多，Prompt 越长，输入成本和干扰风险越高。
3. **推理能力可能受损**：案例化经验容易诱导 LLM 复制旧补丁、旧信号名或错误归纳，削弱独立推理。
4. **记忆权威边界不清**：记忆文本可能未经充分验证便直接影响真实修复，造成 harmful activation。

### 1.4 本计划书解决的问题

本路线设计一种特殊的持续对抗记忆系统，使其同时满足：

- 每轮红蓝对抗的经验均可持久保存；
- 记忆库不会在下一轮被清空；
- 记忆不是自然语言 Prompt；
- 记忆不会直接替代 LLM 推理；
- 记忆通过控制蓝方的诊断、搜索和验证流程发挥作用；
- 只有经过 shadow replay 验证并在当前策略下重新激活的记忆，才能参与真实修复；
- 记忆的保存权、资格权和执行权相互分离；
- 蓝方能够在多轮攻防中累积能力，同时保留正确性门控、可回滚和可审计属性。

---

## 2. 核心设计原则

### 2.1 记忆累积，运行权动态变化

每轮对抗产生的新经验都应进入长期存储：

\[
\mathcal{M}_{\le t}=\mathcal{M}_{\le t-1}\cup\Delta\mathcal{M}_t
\]

其中：

- \(\mathcal{M}_{\le t}\)：截至第 \(t\) 轮的持久记忆库；
- \(\Delta\mathcal{M}_t\)：第 \(t\) 轮新产生或更新的记忆。

记忆对象原则上不被物理删除，而是通过状态变化管理其权限：

```text
candidate
shadow_testing
replay_qualified
active_dormant
reactivated
stale
harmful
superseded
retired
```

核心原则：

> 记忆可以永久保存，但真实运行权必须动态获得、动态撤销。

### 2.2 保存权、资格权、执行权分离

| 权限 | 含义 | 获得条件 |
|---|---|---|
| 保存权 | 进入长期经验或记忆存储 | 轨迹完整、哈希可重建 |
| 资格权 | 可进入 active memory bank | shadow replay 与安全门通过 |
| 执行权 | 可影响当前真实修复 | 当前 case 重新激活通过 |

不能因为一条记忆被保存，就允许它直接影响修复。

### 2.3 记忆不以 Prompt 形式传递

禁止以下机制：

```text
memory text
→ 拼接到 LLM Prompt
→ 影响补丁生成
```

记忆只允许改变蓝方 agent 的结构化执行计划，例如：

- 调用哪些诊断工具；
- 提取哪些 RTL slice；
- 使用多大的波形窗口；
- 如何分配候选生成与 revision 预算；
- 如何排序候选；
- 如何安排 verifier；
- 何时提前停止；
- 是否启用某类局部分析器。

LLM 仍然只读取：

- 当前待修复 RTL；
- 当前 case 的结构化诊断结果；
- 当前 verifier 或 oracle 返回的证据；
- 固定、冻结的任务指令。

### 2.4 Adapter 生成，Runner 验证

> Adapter 负责生成候选与工件，Runner 负责验证事实，Registry 负责裁决运行权，Audit 负责重建全过程。

记忆检索器、诊断工具或 adapter 不得自行宣称：

- 一条记忆有效；
- 一条记忆可执行；
- 一条记忆无害；
- 一条记忆已获得 active 权限。

这些结论必须由 runner 根据可重建证据计算。

### 2.5 Shadow 是授权域，不是临时垃圾区

Shadow 模式不是“测试一次后丢弃”，而是记忆获得正式运行资格的验证域。其职责包括：

- 在不污染 authoritative runtime 的前提下重新激活记忆；
- 验证 trigger 是否准确；
- 验证 control delta 是否有效；
- 测量 helped、harmed 和 neutral；
- 验证跨设计、跨轮次和跨 policy 的兼容性；
- 形成可审计的资格决策。

---

## 3. 目标蓝方状态定义

后续阶段的蓝方状态不应只包含一个 repair policy，而应定义为：

\[
B_t=\left(P_t,\mathcal{E}_{\le t},\mathcal{M}_{\le t},\mathcal{A}_t,R_t,G_t\right)
\]

其中：

- \(P_t\)：当前默认修复、搜索与验证策略；
- \(\mathcal{E}_{\le t}\)：截至当前轮次的不可变经验轨迹；
- \(\mathcal{M}_{\le t}\)：完整持久记忆库；
- \(\mathcal{A}_t\)：当前获得资格的 active-dormant memory bank；
- \(R_t\)：结构化检索器；
- \(G_t\)：shadow replay、重新激活和安全门。

对当前 case \(x\) 的执行计划为：

\[
E_t(x)=\operatorname{Compile}\left(P_t,\operatorname{Reactivate}\left(R_t(\phi(x),\mathcal{A}_t)\right)\right)
\]

---

## 4. 总体系统架构

### 4.1 三层持久化结构

#### 第一层：Immutable Experience Archive

保存每一轮红蓝对抗的完整轨迹，采用 append-only 模式。存储内容包括：

- 当前蓝方 policy hash；
- 红方 poison 与 lineage；
- buggy RTL hash；
- clean RTL hash；
- oracle 工件；
- 蓝方各次修复尝试；
- compile、simulation、formal 和 functional 结果；
- 成功补丁或最终失败；
- 停止规则；
- 诊断输出；
- 记忆激活记录；
- harmful activation；
- token、调用次数和 wall time。

该层是事实层，不直接参与运行。

#### 第二层：Persistent Memory Library

从一个或多个经验轨迹中形成结构化、非语言化记忆。该层存储所有记忆对象，无论其当前状态是未验证、有效、休眠、过时、有害或被替代。状态变化采用新事件记录，禁止覆盖历史。

#### 第三层：Active Memory Bank

保存当前蓝方有资格检索的记忆 ID 集合。Active Memory Bank：

- 与当前 PolicyState 绑定；
- 有独立 hash；
- 与 retriever、activation guard 一同被冻结；
- 只有经过 Whole-Policy Promotion 后才能改变；
- 只包含 `active_dormant` 记忆；
- 记忆进入 bank 后仍然不是无条件执行，而是等待当前 case 重新激活。

### 4.2 高层数据流

```text
Active Blue B_t
    │
    ▼
Red Challenge conditioned on B_t
    │
    ▼
Executable Bug + Oracle Evidence
    │
    ▼
Blue Repair Attempts
    │
    ├── Success trajectory
    └── Residual failure trajectory
    │
    ▼
Immutable Experience Archive
    │
    ▼
Memory Candidate Construction
    │
    ▼
Persistent Memory Library
    │
    ▼
Shadow Replay Qualification
    │
    ▼
Active-Memory-Bank Candidate
    │
    ▼
Whole-Policy Paired Replay
    │
    ▼
Atomic Promotion to B_{t+1}
    │
    ▼
Renewed Red Challenge
```

---

## 5. 记忆对象的定义

### 5.1 记忆不是自然语言经验

禁止存储为主要执行载荷的内容：

- “遇到某类 bug 应该如何修”；
- 历史完整 Prompt；
- 历史完整补丁；
- 源 case 信号名；
- 参考答案；
- 自由文本策略；
- 手工总结的 family rule。

记忆的主要执行载荷应是结构化 control delta。

### 5.2 建议名称

推荐使用：

- **Replay-Activated Adversarial Memory（RAAM）**：重放激活的对抗记忆；
- 工程对象名：`ControlMemory`。

### 5.3 Control Memory Schema

```json
{
  "schema_version": "r3e-control-memory-v1",
  "memory_id": "CM_temporal_lag_001",
  "memory_version": 1,
  "origin_round_id": "R003",
  "source_episode_ids": ["E_R002_017", "E_R003_008"],
  "source_episode_hashes": {
    "E_R002_017": "sha256:...",
    "E_R003_008": "sha256:..."
  },
  "created_under_policy_instance_hash": "sha256:...",
  "created_under_effective_policy_hash": "sha256:...",
  "trigger_predicate": {
    "oracle_stage": "functional_compare",
    "sequential_context": true,
    "temporal_relation": "candidate_lags_golden",
    "cycle_offset_bucket": 1,
    "affected_roles": ["state", "output"]
  },
  "control_delta": {
    "enable_analyzers": ["temporal_alignment", "state_transition_slice"],
    "rtl_slice_mode": "sequential_cone",
    "evidence_window_before": 2,
    "evidence_window_after": 4,
    "candidate_plan": {
      "initial_candidates": 1,
      "revision_rounds": 2
    },
    "candidate_ranking": "verifier_guided",
    "early_stop": "first_verified"
  },
  "status": "candidate",
  "qualification_summary": {},
  "compatibility": {},
  "memory_hash": "sha256:..."
}
```

### 5.4 第一版允许的 Control Delta

#### 诊断控制

- `enable_analyzers`
- `disable_analyzers`
- `evidence_window_before`
- `evidence_window_after`
- `max_signals`
- `rtl_slice_mode`
- `cone_depth`
- `first_divergence_only`

#### 搜索控制

- `initial_candidates`
- `revision_rounds`
- `candidate_batch_size`
- `early_stop`
- `retry_after_compile_fail`
- `retry_after_oracle_fail`

#### 候选评估控制

- `candidate_ranking`
- `pre_oracle_filters`
- `verifier_order`
- `max_changed_blocks`
- `prefer_local_patch`

第一版明确禁止：

- 修改 oracle、testbench、golden RTL；
- 修改 promotion threshold；
- 修改 secret 或 model credentials；
- 注入自由文本 Prompt；
- 直接使用历史 patch；
- 自动执行未经验证的 AST rewrite；
- 无约束修改模型路由；
- 修改 active registry。

---

## 6. 经验轨迹与记忆形成

### 6.1 所有对抗轨迹都应保存

每轮红蓝对抗无论成功或失败，都应产生 `VerifiedEpisode`：

```json
{
  "schema_version": "r3e-verified-episode-v1",
  "episode_id": "E_R004_P021",
  "round_id": "R004",
  "challenged_policy_instance_hash": "sha256:...",
  "challenged_effective_policy_hash": "sha256:...",
  "poison_id": "P021",
  "poison_payload_hash": "sha256:...",
  "buggy_rtl_hash": "sha256:...",
  "oracle_evidence_hash": "sha256:...",
  "failure_descriptor": {},
  "blue_attempts": [],
  "final_outcome": "unresolved",
  "successful_patch_hash": null,
  "activated_memory_ids": [],
  "resource_usage": {
    "input_tokens": 0,
    "output_tokens": 0,
    "llm_calls": 0,
    "verifier_calls": 0,
    "wall_time_seconds": 0
  },
  "episode_hash": "sha256:..."
}
```

### 6.2 记忆候选来源

记忆候选可来自：

1. **成功轨迹对比**：某类诊断流程、搜索编排或候选排序重复帮助修复；
2. **失败轨迹聚类**：多个 unresolved residual 暴露同一诊断缺口；
3. **Shadow helped 事件**：某个临时 control delta 产生稳定的 FAIL→PASS；
4. **Harmful activation 反例**：用于收紧 trigger 或添加禁止条件；
5. **跨轮次复现**：早期形成的模式在后续新设计中再次出现。

### 6.3 记忆形成不等于即时泛化

一条 Control Memory 应明确区分：

- `source support`：来自哪些 episode；
- `trigger hypothesis`：预计何时适用；
- `control hypothesis`：预计改变哪些执行流程；
- `qualification evidence`：实际在哪些冻结 case 上验证；
- `compatibility range`：在哪些 policy 版本下有效。

禁止仅凭单条成功轨迹直接晋升为 active memory。

---

## 7. 记忆关系图

### 7.1 为什么需要关系图

随着红方持续生成新 bug，长期记忆库会不断增长。如果所有记忆平铺存储，将产生重复、过度细分、规则冲突、检索候选过多和 active bank 膨胀等问题。因此需要维护 Memory Graph。

### 7.2 关系类型

- `derived_from`
- `generalizes`
- `specializes`
- `refines`
- `supersedes`
- `conflicts_with`
- `composes_with`
- `equivalent_effective_delta`
- `fails_under_policy`
- `revalidated_under_policy`

### 7.3 示例

```text
M1: one-cycle lag diagnosis
 ├── M4: extra register stage
 ├── M7: current/next state inversion
 └── M9: enable-conditioned lag

M2: boundary counter diagnosis
 ├── M5: comparator off-by-one
 └── M8: terminal-state update ordering
```

新记忆进入库时应先执行：

1. 精确 hash 去重；
2. effective delta 去重；
3. trigger overlap 检查；
4. generalization/specialization 判定；
5. conflict detection；
6. graph relation 写入。

---

## 8. Shadow Replay 资格验证

### 8.1 Shadow 的目标

Shadow Replay 负责回答：

1. 该记忆是否能被准确检索？
2. 该 control delta 是否能改善执行？
3. 是否造成 harmful activation？
4. 是否增加成本？
5. 是否能跨 case、跨 design、跨 round 复用？
6. 是否与当前 policy 兼容？

### 8.2 配对验证

#### Control Arm

```text
Active Policy B_t
→ Default Execution Plan
```

#### Shadow Arm

```text
Active Policy B_t
+ Candidate Control Memory
→ Shadow Execution Plan
```

固定条件：

- 同一 case、seed、模型、总调用预算、verifier、oracle、toolchain；
- 同一 clean/buggy RTL；
- 同一停止上限。

允许变化：

- 诊断器选择；
- evidence slicing；
- 候选与 revision 的内部预算分配；
- verifier 顺序；
- candidate ranking。

### 8.3 Shadow Outcome 分类

| Control | Shadow | 结果 |
|---:|---:|---|
| FAIL | PASS | helped |
| PASS | FAIL | harmed |
| PASS | PASS | neutral_pass |
| FAIL | FAIL | neutral_fail |

同时记录：

- input/output token delta；
- LLM/verifier call delta；
- wall time delta；
- diagnosis coverage；
- trigger precision；
- trigger abstention。

### 8.4 资格门

\[
G_{memory}=G_{retrieval}\land G_{effect}\land G_{safety}\land G_{cost}\land G_{provenance}
\]

#### Retrieval Gate

- trigger 命中具有足够精度；
- 对不相关 case 能正确 abstain；
- 不读取 reference patch 或目标标签；
- 不使用不可观测的 red mutation truth。

#### Effect Gate

- helped 数达到阈值；
- 覆盖多个 case 和 design；
- 相对 default plan 有正增益。

#### Safety Gate

- harmful activation 低于阈值；
- non-target regression 不超过容忍度；
- memory conflict 可控；
- 不修改权威工件。

#### Cost Gate

- 不突破总预算；
- token 不显著增加；
- verifier 调用不失控；
- wall time 不出现不可接受增长。

#### Provenance Gate

- source episode 完整；
- memory hash 可重建；
- control delta 白名单通过；
- current policy binding 正确；
- toolchain fingerprint 一致。

---

## 9. Active-Dormant Memory 与当前 Case 重新激活

### 9.1 Active 不等于始终运行

通过 Shadow Replay 后，记忆进入 `active_dormant`：

- 已获得进入 active memory bank 的资格；
- 可被当前 policy 检索；
- 默认不执行；
- 只有当前 case 重新激活通过时，才获得本次真实执行权。

### 9.2 重新激活流程

```text
Current case
→ Default failure descriptor extraction
→ Retrieve active-dormant memories
→ Current-policy compatibility check
→ Trigger predicate evaluation
→ Conflict check
→ Budget check
→ Compile temporary execution plan
→ Authoritative repair
```

### 9.3 重新激活不得重复跑完整 LLM

重新激活应为低成本、确定性检查，不应先在 shadow 中调用一次 LLM，再在 authoritative lane 中重复调用。它主要检查：

- 当前 failure descriptor 是否满足 trigger；
- 当前 effective policy 是否兼容；
- 所需诊断工具是否可用；
- control delta 是否仍在白名单；
- 是否存在 active memory 冲突；
- 是否满足预算约束；
- 是否被 stale/harmful 状态禁止。

### 9.4 运行时工作集

\[
\mathcal{M}_{work}(x)\subseteq\mathcal{A}_t\subseteq\mathcal{M}_{\le t}
\]

建议第一版：

- retrieval top-k ≤ 3；
- 最终 activation ≤ 1；
- 如需组合，必须是事先验证的 memory bundle；
- 无法确定时回退到 default execution plan。

---

## 10. 真实修复中的记忆作用方式

### 10.1 记忆改变 Agent，不告诉 LLM 答案

正确流程：

```text
Memory activation
→ Change diagnostic/search/verification plan
→ Produce current-case evidence
→ LLM independently reasons
→ Oracle verifies
```

错误流程：

```text
Memory text
→ Tell LLM how to patch
```

### 10.2 示例：一周期滞后记忆

```json
{
  "trigger_predicate": {
    "sequential_context": true,
    "temporal_relation": "candidate_lags_golden",
    "cycle_offset_bucket": 1
  },
  "control_delta": {
    "enable_analyzers": ["temporal_alignment", "state_transition_slice"],
    "rtl_slice_mode": "sequential_cone",
    "evidence_window_before": 2,
    "evidence_window_after": 4,
    "candidate_plan": {
      "initial_candidates": 1,
      "revision_rounds": 2
    }
  }
}
```

真实执行：

```text
检测到 lag-one-cycle
→ 运行时序对齐
→ 定位相关状态寄存器
→ 提取 sequential cone
→ 生成当前 case 的 EvidenceBundle
→ LLM 基于当前证据推理
→ Oracle 验证
```

LLM 不接收历史案例、历史补丁、记忆文本或历史信号名。

---

## 11. Policy 变化与记忆继承

### 11.1 记忆永久保留，运行权重新判定

当蓝方从 \(B_t\) 晋升到 \(B_{t+1}\) 时，所有旧记忆仍保存在 Persistent Memory Library。变化的是：

- 是否仍兼容；
- 是否仍 active；
- 是否需要重新 replay；
- 是否 stale；
- 是否被新记忆 supersede。

### 11.2 三类继承情况

#### A. 静态可证明兼容

如果新 policy 未修改与某条 memory 相关的字段，可继承 active-dormant 权限。

#### B. 需要增量重放

如果新 policy 修改 diagnostic pipeline、evidence slicing、candidate allocation、patch scope 或 verifier ordering，则该 memory 进入 `revalidation_required`，内容保留但执行权暂停。

#### C. 已确认失效

如果在新 policy 下 harmful、无效、被更强 memory 支配或 trigger 不再准确，则进入 `stale`、`harmful` 或 `superseded`。对象和证据仍保留。

### 11.3 Policy 与 Memory Bank 一起晋升

\[
B_{t+1}=\left(P_{t+1},\mathcal{M}_{\le t+1},\mathcal{A}_{t+1},R_{t+1},G_{t+1}\right)
\]

任何以下变化都应触发 Whole-Policy Promotion：

- 新增或删除 active memory；
- 修改 retriever；
- 修改 activation guard；
- 修改 conflict resolver；
- 修改 memory control whitelist。

---

## 12. 红方与记忆系统的闭环

### 12.1 红方不仅攻击修复器，也攻击记忆机制

红方的 capability packet 应包含：

- 当前 effective policy hash；
- active memory bank hash；
- 已覆盖 failure regions；
- 已知 memory blind spots；
- harmful activation regions；
- stale memory regions；
- memory conflict regions；
- 已通过或失败的 replay family。

### 12.2 三类记忆导向红方操作

#### Memory Bypass

构造表面相似、底层机制不同的 bug，测试记忆是否过拟合。

#### Memory Deepening

在已有能力上增加时序深度、组合依赖、控制条件、多模块传播或 reset/enable 交互。

#### Memory Conflict

构造同时匹配多条记忆但 control delta 冲突的 case。

### 12.3 红蓝记忆闭环

```text
Red exposes weakness
→ Blue creates candidate memory
→ Shadow replay qualifies it
→ Memory enters active-dormant bank
→ Future case reactivates it
→ Red attacks the new memory-enabled blue
→ Memory is refined, split, merged, or retired
```

---

## 13. 工程目录建议

```text
r3e/
├── memory/
│   ├── schema.py
│   ├── episode_store.py
│   ├── memory_store.py
│   ├── relation_graph.py
│   ├── candidate_builder.py
│   ├── consolidator.py
│   ├── descriptor.py
│   ├── retriever.py
│   ├── activation_guard.py
│   ├── conflict_resolver.py
│   ├── plan_compiler.py
│   ├── shadow_replay.py
│   ├── qualification_gate.py
│   ├── compatibility.py
│   ├── lifecycle.py
│   ├── metrics.py
│   └── audit.py
├── policy/
├── red/
├── blue/
├── arena/
└── protocol/
```

运行时目录：

```text
runtime/memory/
├── episodes/
├── library/
├── qualification/
├── active_banks/
├── activation/
└── lifecycle/
```

---

## 14. 关键接口设计

```python
class MemoryStore:
    def append_episode(self, episode: VerifiedEpisode) -> str: ...
    def add_candidate(self, memory: ControlMemory) -> str: ...
    def update_lifecycle(self, memory_id: str, event: MemoryLifecycleEvent) -> None: ...
    def get_version(self, memory_id: str, version: int) -> ControlMemory: ...
```

```python
class MemoryRetriever:
    def retrieve(
        self,
        descriptor: FailureDescriptor,
        active_bank: ActiveMemoryBank,
        *,
        top_k: int = 3,
    ) -> list[MemoryMatch]: ...
```

```python
class ActivationGuard:
    def reactivate(
        self,
        *,
        matches: list[MemoryMatch],
        active_policy: PolicyState,
        runtime_context: RuntimeContext,
    ) -> ReactivationDecision: ...
```

```python
class MemoryAwarePlanCompiler:
    def compile(
        self,
        *,
        base_policy: PolicyState,
        reactivation: ReactivationDecision,
    ) -> ExecutionPlan: ...
```

```python
def run_shadow_replay(
    *,
    case: ReplayCase,
    policy: PolicyState,
    memory: ControlMemory,
    seed: int,
    budget: BudgetEnvelope,
) -> ShadowPairedResult: ...
```

---

## 15. 升级阶段路线

### Phase 0：协议权威闭合

**目标**：在引入新记忆系统前，先关闭当前 formal authority 漏洞。

**工作项**：

1. Promotion bundle 重建；
2. Registry 根据证据重新计算 promotion decision；
3. poison payload 全字段不可变；
4. `prepare_validity()` 改为只返回证据；
5. runner 自行验证 oracle 工件；
6. code version 禁止使用 `unknown`；
7. audit 重建 candidate→validity→challenge→archive 全链。

**验收标准**：

- 自洽但伪造的 decision 无法 commit；
- adapter 无法替换 buggy RTL；
- adapter 无法伪造 counterexample；
- 所有权威结论可由 runner 重建。

### Phase 1：不可变经验档案

**目标**：确保每轮红蓝对抗的所有轨迹永久保存。

**工作项**：实现 `VerifiedEpisode`、append-only JSONL、episode/round/policy hash binding、crash-safe 写入和 audit。

**验收标准**：每次对抗均可重建，且不因下一轮或 policy 晋升丢失。

### Phase 2：Control Memory 数据模型

**目标**：建立非 Prompt 化记忆对象和长期库。

**工作项**：schema、control delta 白名单、memory version、source binding、lifecycle event、relation graph、effective delta 去重。

**验收标准**：记忆对象不含 Prompt fragment 或历史完整 patch，所有状态变化不覆盖历史。

### Phase 3：结构化 Failure Descriptor

**目标**：构建不依赖 red truth label 的运行时检索描述符。

**工作项**：first divergence、cycle offset、sequential/combinational context、affected role、assignment type、cone depth、mismatch pattern、descriptor hash。

**验收标准**：descriptor 仅来自当前可观测工件，不读取 reference patch 或 mutation family 真值。

### Phase 4：Shadow Retrieval 与 Reactivation

**目标**：让候选记忆可以在 shadow 域中被检索和重新激活。

**工作项**：high-precision retriever、abstention、compatibility、budget gate、conflict detection、whitelist validation、reactivation event。

**验收标准**：未通过 reactivation 时不会改变 plan；active bank 外或 stale/harmful memory 无法使用。

### Phase 5：Memory-Aware Execution Plan

**目标**：让记忆真正改变蓝方 agent 的执行方式，而非 Prompt。

**工作项**：default/memory-aware compiler、analyzer selection、evidence slicing、candidate/revision allocation、ranking、verifier ordering、resource accounting。

**验收标准**：LLM Prompt 无历史记忆文本，memory token cost 为 0，execution plan hash 可重建。

### Phase 6：Shadow Paired Replay 与资格门

**目标**：验证 Control Memory 的有效性、安全性和成本。

**工作项**：paired runner、target/non-target split、helped/harmed、token/calls/wall time、retrieval precision、cross-design coverage、qualification decision。

**验收标准**：无验证 evidence 的 memory 无法进入 active bank，cost regression 会阻止晋升。

### Phase 7：Active Memory Bank 与 Whole-Policy Promotion

**目标**：将通过验证的记忆纳入完整蓝方状态。

**工作项**：active bank schema/hash、retriever hash、activation guard hash、bank candidate、Whole-Policy replay、atomic promotion、exact rollback。

**验收标准**：active bank 改变必须走 Policy Promotion，registry 中仍只有一个 active PolicyState。

### Phase 8：跨轮累积与兼容性管理

**目标**：保证旧记忆不会随新一轮被清空。

**工作项**：policy transition compatibility、incremental revalidation、stale/harmful/superseded 管理、cross-round reuse、graph merge/split、active bank compression、bounded workset。

**验收标准**：R1 记忆在 R4 仍可检索；policy 变化不会物理删除旧记忆。

### Phase 9：红方记忆导向挑战

**目标**：让红方持续挑战蓝方的记忆机制。

**工作项**：active memory summary、memory bypass/deepening/conflict operator、blind-spot archive、memory-related hardness。

**验收标准**：红方挑战绑定 current policy 与 active bank，memory overfitting 和 conflict 可被主动暴露。

---

## 16. 测试矩阵

### 16.1 存储与不可变性

```text
test_every_round_appends_verified_episodes
test_policy_transition_does_not_delete_memory
test_memory_lifecycle_event_is_append_only
test_memory_version_hash_is_reconstructable
test_episode_archive_survives_resume
```

### 16.2 权限边界

```text
test_candidate_memory_cannot_enter_authoritative_runtime
test_shadow_qualified_memory_requires_bank_promotion
test_active_dormant_memory_requires_reactivation
test_stale_memory_cannot_reactivate
test_harmful_memory_cannot_reactivate
test_memory_cannot_modify_oracle
test_memory_cannot_modify_testbench
test_memory_cannot_write_registry
```

### 16.3 非 Prompt 化约束

```text
test_memory_content_is_not_added_to_prompt
test_history_patch_is_not_added_to_prompt
test_source_signal_names_are_not_replayed
test_memory_token_cost_is_zero
```

### 16.4 检索与激活

```text
test_retriever_uses_runtime_descriptor_only
test_retriever_rejects_reference_patch_features
test_retriever_rejects_red_truth_labels
test_activation_abstains_on_low_confidence
test_activation_is_bound_to_effective_policy_hash
test_activation_rejects_control_delta_conflict
```

### 16.5 Shadow Replay

```text
test_shadow_pair_uses_same_case_seed_model_budget
test_shadow_result_does_not_change_authoritative_hardness
test_shadow_result_does_not_change_residual_archive
test_helped_harmed_classification_is_reconstructable
test_cost_regression_blocks_memory_qualification
```

### 16.6 跨轮累积

```text
test_round_one_memory_is_available_in_round_four
test_policy_change_marks_memory_revalidation_required
test_compatible_memory_inherits_active_status
test_incompatible_memory_is_preserved_but_dormant
test_superseded_memory_remains_auditable
```

### 16.7 Red–Memory 对抗

```text
test_red_memory_bypass_is_policy_bound
test_red_memory_deepening_changes_actual_source_semantics
test_red_memory_conflict_targets_multiple_active_memories
test_red_cannot_read_private_memory_evidence
```

---

## 17. 关键指标

### 17.1 记忆规模

- stored episode count；
- stored/active/dormant/stale/harmful/superseded memory count。

### 17.2 运行效果

- activation coverage；
- activation precision；
- abstention rate；
- helped/harmed/neutral rate；
- cross-design reuse；
- cross-round reuse；
- memory conflict rate。

### 17.3 效率

- input/output token delta；
- LLM/verifier call delta；
- wall time delta；
- RTL slice reduction；
- evidence compression ratio。

### 17.4 持续增强

- frozen replay gain；
- prior-failure recovery；
- memory-enabled repair gain；
- old-memory reuse in future rounds；
- memory retention under policy transition；
- renewed red challenge hardness。

---

## 18. 验收口径

只有同时满足以下条件，才能宣称“记忆真正被用起来”：

1. 记忆来自早期红蓝对抗；
2. 被持久保存；
3. 在后续不同 case 或 design 中被检索；
4. 当前 policy 下重新激活通过；
5. 实际改变执行计划；
6. LLM 未读取历史记忆文本；
7. 真实修复结果或成本出现可观测变化；
8. Oracle 确认结果；
9. 使用过程可被 audit 重建；
10. 记忆继续保留到下一轮。

只有满足以下条件，才能宣称“蓝方持续增强”：

1. 新能力来源于 red-exposed failure；
2. 能力进入持久记忆或完整 policy；
3. 固定 replay 对旧失败有增益；
4. non-target 无不可接受退化；
5. 后续红方挑战针对新蓝方状态；
6. 蓝方可在后续轮次复用早期能力；
7. 提升不能仅由更换测试分布解释。

---

## 19. 风险与边界

### 19.1 记忆库无限增长

措施：effective delta 去重、memory graph 合并、active bank 上限、workset top-k、stale/superseded 不参与检索。

### 19.2 触发器过拟合

措施：trigger 只使用 runtime observable descriptor、shadow precision gate、memory bypass red operator、harmful activation 立即撤权。

### 19.3 Policy 与 Memory 混淆

边界：默认始终生效的配置属于 Policy；条件触发的局部控制增量属于 Memory；Active Memory Bank 是 Whole Policy 的组成部分，但 memory object 保留独立身份和生命周期。

### 19.4 过早引入 AST Rewrite

当前路线只做 diagnosis/search/evaluation control memory；Direct Repair Skill IR 留作后续独立研究。

### 19.5 Shadow 与 Authoritative 混淆

措施：两条 lane 独立；shadow 只产生 qualification evidence；authoritative result 仅来自 active policy；audit 检查数据流权限。

---

## 20. 推荐实施优先级

1. **权威安全**：Registry reconstruction、poison immutability、runner-owned evidence、full audit。
2. **持久经验**：VerifiedEpisode、append-only archive、round/policy/hash binding。
3. **非 Prompt 化记忆**：ControlMemory、descriptor、retriever、activation guard、plan compiler。
4. **Shadow Replay**：paired runner、qualification gate、active bank、Whole-Policy Promotion。
5. **持续累积**：cross-round reuse、compatibility、memory graph、red memory attack operators。

---

## 21. 最终系统原则

1. 每轮经验全部保存。
2. 长期记忆库不随轮次清空。
3. 旧记忆不会因为 policy 晋升被物理删除。
4. 记忆不是 Prompt。
5. 记忆不提供历史答案，而是控制 agent 如何观察、搜索和验证。
6. 保存权不等于运行权。
7. Shadow Replay 决定记忆是否有资格进入 active bank。
8. 当前 case 的重新激活决定记忆是否获得本次执行权。
9. Active Memory Bank 的变化必须通过 Whole-Policy Promotion。
10. Oracle 始终是最终正确性权威。
11. 红方持续攻击蓝方修复器和记忆机制本身。
12. 蓝方增强必须表现为跨轮、跨 case、跨设计的能力复用。

最终数据流：

```text
Red-exposed failure
→ Immutable verified episode
→ Persistent control-memory candidate
→ Shadow paired replay
→ Replay-qualified active-dormant memory
→ Future-case retrieval
→ Current-policy reactivation
→ Memory-aware execution plan
→ LLM independent reasoning
→ Oracle verification
→ Use audit and lifecycle update
→ Memory retained across future rounds
```

---

## 22. 一句话研究定位

> R³E 不把历史经验作为文本提示交给 LLM，而是将每轮红蓝对抗产生的可验证经验持续存储为结构化控制记忆；记忆经 shadow replay 获得资格，并在后续失败满足当前策略绑定条件时重新激活，以改变蓝方的诊断、搜索和验证流程，从而在持续攻防中积累可复用能力。

---

## 23. 后续配套文档

1. `RAAM_PROTOCOL_SPEC.md`：定义 schema、hash、权限和状态机。
2. `RAAM_THREAT_MODEL.md`：定义 adapter、runner、registry、memory store 的信任边界。
3. `RAAM_IMPLEMENTATION_CHECKLIST.md`：按文件和函数拆解工程任务。
4. `RAAM_TEST_MATRIX.md`：独立维护单元、集成和故障注入测试。
5. `RAAM_EXPERIMENT_PROTOCOL.md`：系统稳定后定义持续攻防、跨轮复用和记忆曲线实验。
