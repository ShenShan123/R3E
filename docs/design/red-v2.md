# R³E 深度红蓝对抗与 Grounded Red Discovery 升级计划

> 文档定位：R³E 后续系统升级路线
> 目标里程碑：Grounded Red Discovery V1
> 上游依赖：RAAM Authority Closure V1、Grounded Runtime Receipts
> 核心目标：让红方持续产生真实、有效、语义多样、难度递进且能够推动蓝方能力积累的 RTL 功能缺陷
> 研究边界：蓝方通过 test-time policy evolution 与 RAAM memory accumulation 增强，不涉及 LLM 权重训练

---

## 工程实施状态（2026-07-28，Protocol Foundation）

当前处于红方系统升级阶段，不是模型实验阶段。第一批冻结范围为
**Grounded Red Protocol Foundation V1**：

- [x] GRD-0：25 个 family 的 ontology、family/operator/effect 三类独立
  frozen registry、policy-bound `MutationPlan`、difficulty 和 multi-parent
  lineage schema；
- [x] GRD-1 协议层：runner-owned command receipt、parser semantic-diff
  receipt、runtime-effect receipt、G1–G11 admission decision 的精确重建；
- [x] 四类 append-only archive：valid、residual、covered、rejected；
- [x] coverage state、未覆盖 cell 的确定性选择和 family quota；
- [x] effective policy / effective memory bank 双绑定且脱敏的 red capability
  packet；
- [x] deterministic fake evidence 与多轮 fake system，用于协议、幂等性和
  fail-closed 测试，不作为真实 grounded evidence；
- [ ] GRD-1 执行层：真实 parser/elaboration/compile/simulation/formal/oracle
  command runner 及 provider receipt 接入；
- [ ] GRD-2：10 类 operator 当前只冻结定义、precondition、inverse 和 scope，
  尚未接入 parser-backed AST materializer；
- [ ] GRD-3 至 GRD-8：真实 coverage search、difficulty curriculum、memory
  adversary、red population、controlled composition 和 multi-round pilot。

正式新入口位于 `r3e/red/grounded/`。旧 `r3e.red.validity_gate` 继续用于历史
兼容和 deterministic Whole-Policy 测试，但不得作为 Grounded Red admission
authority。只有可由 `decide_grounded_admission()` 从完整 plan、receipt 和
proof objects 重建的 decision 才能进入新的 formal archives。

协议说明见 `docs/protocols/grounded-red-discovery-foundation-v1.md`，逐项清单见
`docs/checklists/grounded-red-discovery-v1.md`，冻结记录见
`configs/evolution/grounded_red_protocol_foundation_v1.json`。

## 1. 升级背景

R³E 的长期价值不在于对固定 RTL bug benchmark 获得一次性的高修复率，而在于建立如下持续闭环：

```text
当前蓝方状态
→ 红方暴露当前尚未掌握的新缺陷
→ 蓝方尝试修复
→ 保存成功与失败轨迹
→ RAAM 形成并验证可复用能力
→ 新蓝方状态晋升
→ 红方针对新蓝方继续挑战
```

当前系统已经完成：

* Whole-Policy Evolution；
* Policy Registry V2；
* RAAM Protocol Skeleton；
* RAAM Authority Closure V1；
* 持久化经验与记忆；
* Shadow qualification；
* Active Memory Bank；
* policy instance/effective identity 分离。

下一阶段的主要瓶颈将从“蓝方经验能否安全保存”转变为：

> 红方是否能够持续发现足够多、足够真实、足够困难且具有学习价值的新 bug。

若红方始终只生成少量固定 mutation，则会出现：

1. 蓝方逐渐适配有限 mutation operator；
2. RAAM 只积累少数重复 control memory；
3. frozen replay 提升不代表更广泛的能力增强；
4. 红蓝对抗退化为固定训练集上的重复优化；
5. 无法证明系统打破了静态 benchmark 的局限。

因此，红方必须从“简单 mutation generator”升级为：

## Grounded、Coverage-Guided、Policy-Conditioned Bug Discovery Engine

即：

* **Grounded**：每个 bug 都由真实 compile、simulation、formal、oracle 和 semantic diff 证据支撑；
* **Coverage-guided**：主动探索尚未覆盖的 bug family、design、RTL role 和 temporal context；
* **Policy-conditioned**：根据当前蓝方 policy 和 active memory 状态生成针对性挑战；
* **Difficulty-controlled**：缺陷难度可分层、可递进、可验证；
* **Continual**：所有 admitted bugs、residuals 和 covered cases 均长期保留；
* **Learnability-aware**：优先产生能够形成蓝方能力增量的 bug，而不是纯粹不可修复的破坏。

---

# 2. 红方升级的核心目标

## 2.1 目标一：扩大有效 Bug Family

红方需要覆盖更多语义机制，而不是仅增加源代码 mutation 数量。

必须区分：

| 概念                | 含义              |
| ----------------- | --------------- |
| Bug Family        | 缺陷的根本语义机制       |
| Mutation Operator | 如何修改 RTL        |
| Runtime Effect    | 缺陷在执行中的表现       |
| Context           | 缺陷所在的设计、角色和时序环境 |
| Bug Instance      | 某个具体 RTL 中的一次缺陷 |

例如：

```text
Family:
  Counter boundary error

Operator:
  < → <=

Runtime effect:
  Terminal state delayed by one cycle

Context:
  Sequential counter termination

Instance:
  counter.v 中某个比较表达式
```

同一个 operator 不能被错误统计为多个 family。

---

## 2.2 目标二：提升当前蓝方的挑战难度

难度不能只用修改行数衡量。

红方需要逐步增加：

* temporal depth；
* dependency depth；
* structural scope；
* activation rarity；
* control-condition complexity；
* family composition depth；
* repair ambiguity；
* current-policy failure rate。

红方应优先生成处于蓝方能力前沿的 bug：

```text
过于简单
→ 蓝方无需形成新能力

适中困难
→ 最有利于 policy/memory 学习

完全不可处理
→ 只能形成长期 residual，学习价值有限
```

---

## 2.3 目标三：确保所有 Bug 真实有效

每个 admitted bug 必须满足：

```text
Clean RTL passes
→ Mutated RTL parses/elaborates/compiles
→ Mutated RTL produces reproducible functional failure
→ Testbench and oracle remain unchanged
→ Inverse mutation restores clean behavior
→ Parser-backed semantic diff matches declared family
```

任何基础设施异常、超时、随机失败或非法源码破坏，都不能被统计为有效红方发现。

---

## 2.4 目标四：让红方产生可学习压力

红方发现的 bug 应尽量形成以下至少一种结果：

* 蓝方成功修复并生成 verified trajectory；
* 当前 policy 下产生稳定 residual；
* 形成新的 FailureDescriptor cluster；
* 产生新的 ControlMemory candidate；
* 对已有 memory 形成 bypass、deepening 或 conflict；
* 暴露旧 policy 或旧 memory 的遗忘问题；
* 形成后续可重放的 capability test。

红方不应仅追求：

```text
让蓝方失败
```

而应追求：

```text
暴露一个能够推动蓝方能力结构发生变化的真实弱点
```

---

# 3. 目标系统总体架构

建议将红方系统表示为：

[
R_t =
\left(
\Omega,
\Gamma_t,
\mathcal A_t,
C_t,
S_t,
G_t
\right)
]

其中：

* (\Omega)：Bug Family Ontology；
* (\Gamma_t)：当前可用 mutation operator portfolio；
* (\mathcal A_t)：历史 bug archive 与 lineage graph；
* (C_t)：当前蓝方 capability packet；
* (S_t)：coverage-guided search controller；
* (G_t)：grounded bug admission gates。

完整数据流：

```text
Active Blue Policy B_t
+ Active Memory Summary
+ Residual/Covered Archive
+ Family Coverage Matrix
                │
                ▼
      Red Coverage Planner
                │
                ▼
    Structured Mutation Plan
                │
                ▼
 Parser-Constrained Mutation
                │
                ▼
 Runner-Owned Validity Execution
                │
                ▼
 Semantic Diff + Runtime Effect
                │
                ▼
 Hardness / Novelty / Learnability
                │
                ▼
      Admitted Bug Archive
                │
                ▼
       Blue Repair Challenge
                │
                ▼
 Verified Episode / Residual
                │
                ▼
         RAAM Evolution
                │
                ▼
       Promoted Blue B_{t+1}
                │
                ▼
       Renewed Red Challenge
```

---

# 4. Bug Family Ontology V1

第一版应优先覆盖容易由 parser、simulation 和 oracle 严格验证的功能性缺陷。

## 4.1 组合逻辑与数据语义

### F01 Expression Operator Error

* 算术操作符错误；
* 逻辑操作符错误；
* 位运算操作符错误；
* 移位方向或移位量错误。

示例：

```text
+ ↔ -
& ↔ |
&& ↔ ||
<< ↔ >>
```

### F02 Comparator and Boundary Error

* `<` 与 `<=`；
* `>` 与 `>=`；
* `==` 与 `!=`；
* 终止条件；
* 边界常数错误。

### F03 Predicate Polarity Error

* 条件取反；
* enable 极性错误；
* branch condition polarity；
* valid/ready polarity。

### F04 Mux and Priority Error

* 分支交换；
* priority 顺序错误；
* default branch 错误；
* case item 漏失；
* priority/unique semantics 破坏。

### F05 Width and Truncation Error

* slice 范围错误；
* bit width 错误；
* 拼接缺失；
* 高位或低位截断；
* 扩展宽度错误。

### F06 Signedness and Casting Error

* signed/unsigned 混用；
* sign extension 错误；
* zero extension 错误；
* `$signed`/`$unsigned` 使用错误。

### F07 Arithmetic Boundary Semantics

* carry 丢失；
* borrow 错误；
* overflow 处理；
* saturating arithmetic；
* modulus/wrap 错误。

---

## 4.2 时序与状态语义

### F08 Blocking/Nonblocking Assignment Error

* sequential block 中 `=`/`<=` 混用；
* 组合块错误使用 nonblocking；
* 更新顺序依赖错误。

### F09 Current-State/Next-State Confusion

* 使用 current state 替代 next state；
* next-state logic 接错；
* output 基于错误状态版本。

### F10 Reset Semantics Error

* reset polarity；
* synchronous/asynchronous reset；
* reset value；
* reset priority；
* partial state reset。

### F11 Enable and Hold Error

* enable 条件遗漏；
* hold 行为错误；
* disable 时仍更新；
* gated update 范围错误。

### F12 One-Cycle Latency Error

* 多一级寄存器；
* 少一级寄存器；
* valid 与 data 错位；
* output 延迟一周期。

### F13 Counter and Index Error

* off-by-one；
* terminal count；
* wrap condition；
* address increment；
* loop index；
* FIFO pointer。

### F14 State Update Ordering Error

* 状态、计数器和输出更新顺序错误；
* transition 和 side effect 不一致；
* terminal state 的输出更新过早或过晚。

---

## 4.3 控制、协议与存储语义

### F15 FSM Transition Error

* transition condition 错误；
* target state 错误；
* missing transition；
* unreachable/redundant transition；
* premature terminal state。

### F16 FSM Output Semantics

* Moore/Mealy 混用；
* state output 错误；
* transition-cycle output 错误；
* output clear/set 时机错误。

### F17 Valid/Ready Handshake Error

* valid 未保持；
* ready 条件错误；
* data 在 stall 时变化；
* transaction 重复消费或丢失。

### F18 Stall/Flush/Pipeline Control Error

* stall 未冻结全部状态；
* flush 范围错误；
* bubble 插入错误；
* branch recovery 错误；
* pipeline stage misalignment。

### F19 Memory Read/Write Semantics

* address 错误；
* read/write enable；
* byte enable；
* read latency；
* write-first/read-first；
* concurrent read/write priority。

### F20 Request/Response Lifecycle Error

* request 提前结束；
* response 对应错误；
* outstanding transaction tracking；
* done/ack 时机错误。

---

## 4.4 结构与参数语义

### F21 Cross-Module Wiring Error

* 端口错接；
* 信号位序错误；
* enable/reset 跨模块接错；
* bus lane mapping 错误。

### F22 Parameter-Dependent Error

* 特定 width 下失效；
* parameter boundary；
* `$clog2`；
* generate 条件；
* zero/one depth 特例。

### F23 Generate and Array Index Error

* generate loop bound；
* instance index；
* packed/unpacked dimension；
* array select；
* lane mapping。

### F24 Multi-Block Dependency Error

* 多个 always block 的 dependency；
* shared state update；
* control/data block 不一致；
* cross-block ordering。

---

## 4.5 组合型缺陷

### F25 Controlled Family Composition

仅允许组合两个已经独立验证的 family，例如：

```text
counter boundary
+
one-cycle latency
```

或：

```text
valid/ready hold
+
FSM transition condition
```

第一版 composition depth 最大为 2，禁止一次生成不可解释的多点随机破坏。

---

# 5. Family、Operator 与 Effect 分离

建议建立三个独立 registry。

## 5.1 Family Registry

```json
{
  "family_id": "temporal.one_cycle_latency",
  "family_group": "sequential",
  "description": "An additional or missing register stage changes observable latency.",
  "required_context": [
    "sequential"
  ],
  "supported_oracles": [
    "cycle_level_differential"
  ],
  "minimum_semantic_proof": [
    "register_stage_delta",
    "runtime_temporal_offset"
  ]
}
```

## 5.2 Operator Registry

```json
{
  "operator_id": "insert_register_stage",
  "supported_family_ids": [
    "temporal.one_cycle_latency"
  ],
  "preconditions": [
    "single_clock_domain",
    "target_expression_is_observable"
  ],
  "maximum_changed_modules": 1,
  "inverse_operator": "remove_inserted_register_stage",
  "executor": "ast_template"
}
```

## 5.3 Runtime Effect Registry

```json
{
  "effect_id": "candidate_lags_golden_one_cycle",
  "observable_features": {
    "temporal_relation": "candidate_lags_golden",
    "cycle_offset_bucket": 1
  }
}
```

同一 family 可以对应多个 operator 和多个 runtime effects。

---

# 6. Structured Mutation Plan

红方 LLM 或 planner 不应直接修改完整 RTL，而应先生成结构化 MutationPlan。

```json
{
  "schema_version": "r3e-grounded-mutation-plan-v1",
  "plan_id": "MP_R004_018",
  "challenged_policy_instance_hash": "sha256:...",
  "challenged_effective_policy_hash": "sha256:...",
  "target_design": "controller",
  "target_module": "controller_top",
  "target_ast_node_hash": "sha256:...",
  "family_id": "temporal.one_cycle_latency",
  "operator_id": "insert_register_stage",
  "expected_runtime_effect_id": "candidate_lags_golden_one_cycle",
  "preconditions": {
    "sequential_context": true,
    "single_clock_domain": true
  },
  "scope_limits": {
    "maximum_changed_modules": 1,
    "maximum_changed_blocks": 1,
    "maximum_ast_edits": 3
  },
  "difficulty_target": {
    "difficulty_band": "D2",
    "dependency_depth_delta": 1,
    "temporal_depth_delta": 1
  },
  "lineage": {
    "parent_poison_ids": [],
    "evolution_operator": "fresh"
  },
  "plan_hash": "sha256:..."
}
```

MutationPlan 是非权威 proposal。Runner 必须重新验证：

* AST node 是否存在；
* preconditions 是否成立；
* scope 是否满足；
* semantic diff 是否匹配；
* runtime effect 是否真实发生。

---

# 7. Mutation 执行分级

## Tier 1：确定性 AST Operator

第一阶段优先实现：

* comparator replacement；
* predicate negation；
* constant boundary；
* slice shift；
* signedness cast；
* blocking/nonblocking；
* reset polarity/value；
* enable condition；
* FSM transition target；
* counter terminal condition。

优势：

* 可重建；
* 易于 inverse；
* 易于 semantic diff；
* 较低无效率；
* family 标签清晰。

## Tier 2：结构模板 Operator

用于：

* register stage 插入/删除；
* valid/data misalignment；
* pipeline stall；
* FSM output timing；
* memory read latency；
* multi-block dependency。

这些 operator 需要：

* parser-backed symbol table；
* AST binding；
* clock/reset analysis；
* generated signal naming；
* inverse transformation。

## Tier 3：LLM-Proposed Transformation

仅在确定性 operator 无法表达时使用。

LLM 只能输出：

* mutation intent；
* target region；
* expected family；
* source edit proposal。

Runner 必须：

* 限定 changed region；
* parse 全文件；
* 执行 semantic diff；
* 验证 scope；
* 验证 runtime effect；
* 执行 minimization。

LLM 生成的源码本身不具有任何 authority。

---

# 8. Grounded Bug Admission Gate

每个 candidate 必须依次通过以下硬门。

## G1 Source Integrity Gate

验证：

* clean RTL hash；
* testbench hash；
* oracle hash；
* build configuration；
* allowed file manifest；
* mutation target；
* poison payload hash。

禁止修改：

* testbench；
* golden output；
* oracle；
* timeout threshold；
* verifier；
* top module；
* secret evaluation cases。

## G2 Parser and Elaboration Gate

验证：

* parser 成功；
* module hierarchy 完整；
* port interface 合法；
* generate 展开；
* parameter binding；
* target AST node 存在；
* operator preconditions 成立。

## G3 Clean Baseline Gate

Runner 重新执行 clean RTL：

```text
parse
→ elaborate
→ compile
→ simulation/formal
→ oracle pass
```

Clean baseline 不通过，则该 design/case 不允许进入 red mutation。

## G4 Poison Executability Gate

要求 mutated RTL：

* parse 成功；
* elaborate 成功；
* compile 成功；
* 仿真可完成；
* 不依赖 simulator crash；
* 不依赖 timeout；
* 不产生无界 stdout；
* 不通过基础设施异常制造失败。

## G5 Functional Failure Gate

要求：

```text
Clean: Pass
Poison: Functional Fail
```

并生成 runner-owned：

* compile receipt；
* simulation receipt；
* oracle receipt；
* first-divergence receipt；
* output artifact hashes。

## G6 Determinism Gate

同一 poison 至少重复运行验证：

* oracle verdict 相同；
* first divergence 相同；
* mismatch topology 相同；
* effect signature 相同。

不稳定 poison 进入 rejected archive，不得进入正式对抗。

## G7 Revert Proof Gate

Runner 保存 inverse transformation，并验证：

```text
Poison
+ inverse mutation
→ original clean semantic hash
→ oracle pass
```

Inverse mutation 仅用于 validity，不得向蓝方暴露。

## G8 Semantic Family Proof Gate

Parser-backed semantic diff 必须证明：

* 声明的 AST 修改真实存在；
* family 标签与修改一致；
* changed module/block 数不超过计划；
* 没有未声明的 collateral edits；
* normalized AST diff 可重建。

## G9 Runtime Effect Proof Gate

Artifact-derived FailureDescriptor 必须与预期 effect 基本一致。

例如 one-cycle latency：

```text
Semantic proof:
  register stage delta = +1

Runtime proof:
  candidate output lags golden by one cycle
```

仅 semantic diff 不足以证明真实 runtime effect。

## G10 Non-Triviality Gate

拒绝：

* 将输出直接固定为常量；
* 删除整个 always block；
* 删除主要模块；
* 制造全局 X；
* 永不拉高 done；
* 无限制死循环；
* 修改不可达代码；
* 只修改注释；
* 只修改无关信号；
* 同时破坏大量不相关逻辑。

## G11 Mutation Minimization Gate

对通过 validity 的 candidate 执行 delta debugging：

```text
尝试移除部分 AST edits
→ 重跑 grounded oracle
→ 保留最小仍能产生相同功能失败的 mutation
```

最终 archive 保存 minimized poison，而非原始冗余版本。

---

# 9. Bug Difficulty Model

## 9.1 多轴 Difficulty Profile

```json
{
  "schema_version": "r3e-red-difficulty-profile-v1",
  "current_blue_failure_rate": 0.67,
  "temporal_depth": 2,
  "dependency_depth": 3,
  "changed_module_count": 1,
  "changed_block_count": 1,
  "activation_rarity": 0.25,
  "composition_depth": 1,
  "repair_locality": "cross_block",
  "candidate_ambiguity": 2,
  "difficulty_band": "D2",
  "profile_hash": "sha256:..."
}
```

## 9.2 Difficulty Bands

### D0：Local Semantic Mutation

* 单表达式；
* 单 block；
* 直接可观测；
* 无跨周期传播。

用于：

* operator 验证；
* 基础 family coverage；
* 初始系统校准。

### D1：Local Sequential Mutation

* 单 block；
* 涉及时序或状态；
* 一周期内暴露；
* 修复仍为局部。

### D2：Dependency-Deep Mutation

* 多级依赖；
* 延迟多个周期暴露；
* 需要 cone slicing；
* 可能涉及多个 block。

### D3：Structural or Rare-Activation Mutation

* 跨 block 或跨 module；
* 稀有触发条件；
* handshake/pipeline/memory；
* 多个合理但错误的候选补丁。

### D4：Controlled Composition

* 两个已验证 family；
* composition depth ≤ 2；
* 每个 family 都有独立 lineage；
* 仍然具备明确 oracle 和 minimization。

第一版 Grounded Red Discovery 建议只正式开放 D0–D3。D4 在基础 family admission 稳定后启用。

---

# 10. 动态 Difficulty Curriculum

红方不应从第一轮开始全部生成高难度 bug。

建议按当前蓝方表现动态调整。

## 初始阶段

```text
D0: 40%
D1: 40%
D2: 20%
D3/D4: 0%
```

目标：

* 验证 ontology；
* 校准 operator validity；
* 建立 family coverage。

## 中间阶段

```text
D0: 15%
D1: 35%
D2: 35%
D3: 15%
```

目标：

* 增加 temporal/dependency depth；
* 形成可复用 RAAM memory；
* 测试跨设计迁移。

## 成熟阶段

```text
D0: 10%
D1: 20%
D2: 35%
D3: 25%
D4: 10%
```

具体比例不应永久硬编码，应由 coverage planner 根据以下指标动态决定：

* family saturation；
* blue repair rate；
* memory coverage；
* red admission yield；
* residual stability；
* false activation；
* available validation budget。

---

# 11. 红方 Bug 类型分配

每轮 admitted bugs 建议分为三类。

## 11.1 Frontier Bugs

定义：

* 当前蓝方部分 seed 成功、部分失败；
* 或默认 plan 失败、扩展搜索成功；
* 或单蓝失败、population blue 部分成功。

价值：

* 最适合形成稳定 policy transition；
* 最适合产生 helped memory；
* 修复难度适中。

建议占 admitted pool 的约 40%–60%。

## 11.2 Hard-but-Repairable Residuals

定义：

* 当前蓝方所有固定 seed 失败；
* 但更大预算、oracle-guided search 或 stronger blue 可偶尔修复；
* inverse mutation 明确；
* bug scope 受控；
* 不属于基础设施失败。

价值：

* 推动蓝方能力上限；
* 用于 multi-round transition；
* 测试长期 memory accumulation。

建议占约 20%–40%。

## 11.3 Exploratory Bugs

定义：

* 新 family；
* 新 design；
* 新 RTL role；
* 新 runtime effect；
* 新 family composition；
* 新 memory challenge。

价值：

* 防止红方只围绕已知分布优化；
* 扩大长期能力空间。

建议占约 10%–30%。

---

# 12. Coverage-Guided Search

## 12.1 Coverage Matrix

维护：

```text
Family
× Design
× RTL Role
× Temporal Context
× Difficulty Band
× Effective Blue Policy
× Active Memory Definition
```

示例：

| Family    | Design     | Role     | Context       | D0 | D1 | D2 | D3 |
| --------- | ---------- | -------- | ------------- | -: | -: | -: | -: |
| Reset     | controller | state    | sequential    |  ✓ |  ✓ |  — |  — |
| Handshake | fifo       | valid    | sequential    |  — |  ✓ |  ✓ |  — |
| Width     | alu        | datapath | combinational |  ✓ |  — |  — |  — |

Planner 优先探索：

* 从未覆盖的 family；
* 只在单一 design 出现的 family；
* 未覆盖的 role/context；
* 当前 active memory 未覆盖区域；
* 长期无新发现的 saturated operator 替代方案。

---

## 12.2 Novelty Signature

每个 admitted bug 建议构造：

```text
semantic family
+ operator
+ normalized AST diff
+ runtime effect signature
+ FailureDescriptor cluster
+ design context
+ difficulty profile
```

不能仅以：

* source file hash；
* line number；
* poison ID；

判断 novelty。

---

# 13. Red Fitness 与选择机制

## 13.1 第一层：硬门

以下任一失败直接拒绝：

```text
invalid clean baseline
parse/elaboration failure
compile failure
no functional mismatch
nondeterminism
oracle/testbench tampering
semantic-family mismatch
nontriviality failure
revert proof failure
```

## 13.2 第二层：Pareto 排序

对通过硬门的 bug，使用多目标排序：

* current-blue hardness；
* family novelty；
* runtime-effect novelty；
* design-region novelty；
* temporal/dependency depth；
* memory challenge value；
* learnability；
* minimization quality；
* validation cost。

不要将所有目标过早压成一个标量 fitness，否则红方容易通过生成某种极端 bug 刷分。

## 13.3 Archive Quota

为了防止高产 family 垄断：

* 单个 family 占比设上限；
* 单个 operator 占比设上限；
* 单个 design 占比设上限；
* 简单 D0 bug 占比随轮次降低；
* composition bug 占比受控。

具体阈值在 pilot 后冻结。

---

# 14. Learnability 评估

每个 admitted bug 增加：

```json
{
  "learnability_status": "frontier",
  "learnability_evidence": {
    "default_blue_successes": 1,
    "extended_blue_successes": 2,
    "population_blue_successes": 3,
    "diagnostic_reproducible": true,
    "memory_candidate_created": false
  }
}
```

建议状态：

* `frontier`；
* `hard_but_repairable`；
* `diagnostic_only`；
* `unresolved_exploratory`；
* `infrastructure_failure`；
* `invalid_mutation`。

只有前四类可以进入研究分析。后两类只能进入 rejection/audit 记录。

---

# 15. Red Population 组织

红方不应只由一个通用 LLM 完成全部任务。

## 15.1 Coverage Planner

职责：

* 分析 coverage matrix；
* 选择 family、design、role、difficulty；
* 分配预算；
* 避免 family 饱和。

## 15.2 Family Specialists

建议配置：

* arithmetic/width specialist；
* temporal/state specialist；
* FSM/control specialist；
* protocol/memory specialist；
* structural/parameter specialist。

Family Specialist 主要生成 MutationPlan，不直接获得源码 authority。

## 15.3 Operator Executor

确定性执行 AST 或 template rewrite。

该组件不使用 LLM，保证：

* 可重建；
* 可逆；
* scope 有界；
* diff 清晰。

## 15.4 Hardness Escalator

针对已有 admitted poison 执行：

* deepen；
* transfer；
* compose；
* rare-trigger refinement。

## 15.5 Memory Adversary

专门挑战当前 Active Memory Bank：

* memory bypass；
* memory deepening；
* memory conflict；
* memory transfer；
* stale-condition discovery。

## 15.6 Validator

负责：

* grounded receipts；
* parser/elaboration；
* clean/poison oracle；
* determinism；
* semantic diff；
* effect signature；
* admission decision。

Validator 必须与 generator 分离。

## 15.7 Minimizer

负责：

* AST delta reduction；
* 去除无关 edit；
* 保持相同 oracle failure；
* 输出 canonical minimal poison。

---

# 16. Memory-Conditioned Red Challenge

红方可以读取经过脱敏的 Active Memory Capability Packet。

```json
{
  "effective_blue_policy_hash": "sha256:...",
  "effective_memory_bank_hash": "sha256:...",
  "covered_failure_regions": [],
  "known_memory_blind_spots": [],
  "known_false_activation_regions": [],
  "stale_memory_regions": [],
  "memory_conflict_regions": [],
  "family_coverage_summary": {}
}
```

红方不得读取：

* memory source episodes；
* historical patches；
* LLM Prompt；
* private rationale；
* reference repair；
* hidden evaluation cases。

---

## 16.1 Memory Bypass

目标：

* runtime descriptor 与已有 memory 相似；
* 底层 semantic mechanism 不同；
* 检验 memory 是否仅依赖表面模式。

示例：

```text
已有 memory:
  one-cycle lag caused by extra register

Bypass:
  one-cycle lag caused by next-state/current-state confusion
```

---

## 16.2 Memory Deepening

在相同 family 上增加：

* dependency depth；
* temporal depth；
* control condition；
* multi-block propagation；
* parameter dependence。

---

## 16.3 Memory Conflict

生成同时匹配两个 memory trigger 的 bug，但两个 control delta：

* 不兼容；
* 顺序相关；
* 产生不同 analyzer/slice 需求。

目标不是强行使系统失败，而是检验：

* retriever；
* conflict resolver；
* abstention；
* memory bundle qualification。

---

## 16.4 Memory Transfer

将已有 family 转移至：

* 新 design；
* 新 RTL role；
* 新 parameter；
* 新 temporal context；
* 新 structural scope。

该类 bug 最适合检验记忆是否真正具有跨设计复用能力。

---

# 17. 红方 Archive 体系

## 17.1 Valid Bug Archive

保存全部通过 Grounded Admission 的 bug：

* 已修复；
* 未修复；
* frontier；
* exploratory；
* memory challenge。

## 17.2 Residual Archive

保存当前蓝方不能稳定修复的 bug。

Residual 不因 policy 变化被删除，应记录：

* 首次暴露 policy；
* 当前 policy；
* 每轮 replay；
* difficulty；
* family；
* lineage；
* memory interaction。

## 17.3 Covered Archive

保存蓝方已稳定修复的 bug。

用途：

* frozen replay；
* forgetting detection；
* policy promotion；
* memory revalidation；
* red deepening；
* held-out analysis。

## 17.4 Rejected Archive

保存：

* compile failure；
* nondeterministic；
* semantic mismatch；
* no functional effect；
* oracle tampering；
* nontriviality rejection；
* duplicate；
* minimization failure。

Rejected archive 对 operator 质量分析非常重要。

---

# 18. Bug Lineage Graph

所有非 fresh bug 必须有 lineage。

```json
{
  "poison_id": "P_R005_021",
  "parent_poison_ids": [
    "P_R003_009"
  ],
  "lineage_operator": "memory_deepening",
  "source_family_ids": [
    "temporal.one_cycle_latency"
  ],
  "target_family_ids": [
    "temporal.one_cycle_latency"
  ],
  "difficulty_delta": {
    "temporal_depth": 1,
    "dependency_depth": 2
  },
  "semantic_diff_receipt_hash": "sha256:...",
  "lineage_hash": "sha256:..."
}
```

支持关系：

* `derived_from`；
* `deepens`；
* `bypasses`；
* `transfers`；
* `composes_with`；
* `minimized_from`；
* `invalidates_memory`。

---

# 19. 与 RAAM 的完整闭环

## 19.1 每轮流程

```text
1. Load active blue B_t
2. Load active memory capability
3. Plan red coverage targets
4. Generate grounded mutation plans
5. Execute AST/template mutations
6. Run grounded admission
7. Select admitted frontier/hard/exploratory bugs
8. Challenge blue B_t
9. Save all VerifiedEpisodes
10. Build/extend persistent ControlMemory
11. Shadow qualify candidate memories
12. Build memory-bank child
13. Whole-policy replay and promotion
14. Obtain B_{t+1}
15. Renew red challenge against B_{t+1}
```

## 19.2 闭环中的能力形成

```text
Red bug family expansion
→ More diverse residual descriptors
→ More diverse control-memory candidates
→ RAAM qualification
→ Active memory reuse
→ Blue policy improvement
→ Red bypass/deepening
→ Memory refinement or split
```

## 19.3 记忆更新结果

红方新挑战可能导致旧 memory：

* 被再次验证；
* 获得更多 evidence；
* 被 specialization；
* 被 generalization；
* 被拆分；
* 与另一条 memory 组合；
* 进入 stale；
* 被 harmful 证据撤权；
* 被新 memory supersede。

---

# 20. Grounded Red Discovery 工程目录

```text
r3e/red/grounded/
├── ontology.py
├── family_registry.py
├── operator_registry.py
├── effect_registry.py
├── mutation_plan.py
├── planner.py
├── coverage.py
├── curriculum.py
├── fitness.py
├── admission.py
├── determinism.py
├── revert_proof.py
├── minimizer.py
├── novelty.py
├── lineage.py
├── archives.py
├── capability_packet.py
├── memory_adversary.py
├── metrics.py
└── audit.py

r3e/red/operators/
├── expression.py
├── comparator.py
├── width.py
├── signedness.py
├── reset.py
├── enable.py
├── assignment.py
├── counter.py
├── fsm.py
├── pipeline.py
├── handshake.py
├── memory_access.py
├── structural.py
└── composition.py
```

Grounded runtime 共用：

```text
r3e/grounded/
├── command_runner.py
├── receipts.py
├── parser.py
├── elaboration.py
├── compile.py
├── simulation.py
├── formal.py
├── oracle.py
├── failure_descriptor.py
└── semantic_diff.py
```

---

# 21. 关键协议对象

建议新增：

```text
r3e-grounded-mutation-plan-v1
r3e-red-family-definition-v1
r3e-red-operator-definition-v1
r3e-red-runtime-effect-v1
r3e-red-difficulty-profile-v1
r3e-red-semantic-diff-receipt-v1
r3e-red-effect-receipt-v1
r3e-red-admission-decision-v1
r3e-red-lineage-v1
r3e-red-coverage-state-v1
r3e-red-capability-packet-v1
```

---

# 22. 实施里程碑

## GRD-0：Ontology and Protocol

### 工作项

* 定义 family/operator/effect；
* 建立初始 20+ family；
* 定义 MutationPlan；
* 定义 AdmissionDecision；
* 定义 DifficultyProfile；
* 定义 lineage。

### 验收

* family、operator、effect 不混用；
* 所有 schema 可重建 hash；
* 不允许未知 family 进入 formal mode；
* family registry 有版本。

---

## GRD-1：Grounded Admission Foundation

### 工作项

* runner-owned command receipts；
* parser/elaboration receipts；
* compile/simulation/oracle receipts；
* clean baseline；
* poison execution；
* determinism；
* revert proof。

### 验收

* adapter 自报 `oracle_ok` 无效；
* false validity acceptance 为 0；
* timeout/crash 不计为功能 bug；
* clean/poison/revert 三条链可审计。

---

## GRD-2：Core AST Operator Library

### 第一批 operator

1. comparator；
2. predicate polarity；
3. boundary constant；
4. width/slice；
5. signedness；
6. reset；
7. enable；
8. blocking/nonblocking；
9. counter terminal；
10. FSM transition。

### 验收

* 每个 operator 有 precondition；
* 每个 operator 有 inverse；
* semantic diff 精确匹配；
* mutation scope 有界；
* operator 单元测试覆盖。

---

## GRD-3：Coverage-Guided Planner

### 工作项

* coverage matrix；
* family saturation；
* design/role/context coverage；
* exploration budget；
* archive quotas；
* Pareto ranking。

### 验收

* 不再重复集中于单一 family；
* planner 能主动选择未覆盖单元；
* coverage state 可恢复；
* 同一输入产生确定性计划。

---

## GRD-4：Difficulty Curriculum

### 工作项

* DifficultyProfile；
* D0–D3；
* temporal deepening；
* dependency deepening；
* rare trigger；
* cross-block；
* frontier/hard/exploratory 分类。

### 验收

* 难度来自 grounded evidence；
* 修改行数不能充当难度；
* curriculum 随蓝方表现调整；
* 不允许直接跳过 validity 追求高难度。

---

## GRD-5：Memory-Conditioned Red

### 工作项

* Active Memory Capability Packet；
* bypass；
* deepening；
* conflict；
* transfer；
* stale-condition discovery。

### 验收

* 红方不读取 source episode 或 patch；
* memory challenge 与 current effective bank 绑定；
* bypass/deepening 有 semantic proof；
* conflict 作用于真实不同 memory definitions。

---

## GRD-6：Red Population

### 工作项

* family specialists；
* coverage explorer；
* hardness escalator；
* memory adversary；
* validator；
* minimizer；
* resource scheduler。

### 验收

* generator 与 validator 权限分离；
* 多模型输出保留独立 provenance；
* 不允许多个模型无解释地共同重写 RTL；
* population gain 可被单因子分析。

---

## GRD-7：Controlled Composition

### 工作项

* composition depth 2；
* parent family lineage；
* sequential operator application；
* intermediate validity；
* final minimization。

### 验收

* 每个组成 family 单独可验证；
* composition 非随机多点修改；
* 可区分 family interaction 与单 family 难度；
* 组合 bug 仍有明确 oracle。

---

## GRD-8：Multi-Round Red–Blue Pilot

### 推荐配置

* 4 个具备稳定 testbench 的 designs；
* 8 个核心 families；
* 3 seeds；
* 2–3 rounds；
* 每轮固定 red proposal budget；
* target/non-target replay；
* 1–3 条 active memories；
* deterministic operators 为主；
* 单一真实 LLM planner。

### 主要验证

* 红方 family coverage 随轮次增加；
* 有效 bug yield 不显著崩溃；
* 蓝方 frozen replay 提升；
* 早期 memory 在后续轮次被复用；
* red 能对 active memory 产生有效 bypass/deepening；
* harmful activation 保持为零或严格受控；
* held-out family/design 不被污染。

---

# 23. 测试矩阵

## 23.1 Validity

```text
test_clean_baseline_must_pass
test_poison_must_compile
test_poison_must_functionally_fail
test_timeout_is_not_functional_bug
test_crash_is_not_functional_bug
test_revert_restores_clean_semantics
test_testbench_change_is_rejected
test_oracle_change_is_rejected
test_nondeterministic_poison_is_rejected
```

## 23.2 Semantic Family

```text
test_family_label_matches_parser_diff
test_operator_precondition_is_enforced
test_changed_scope_respects_plan
test_comment_only_change_is_rejected
test_unreachable_change_is_rejected
test_extra_collateral_edit_is_rejected
```

## 23.3 Difficulty

```text
test_temporal_depth_is_artifact_derived
test_dependency_depth_is_parser_derived
test_line_count_does_not_define_difficulty
test_composition_depth_is_bounded
test_difficulty_curriculum_respects_budget
```

## 23.4 Coverage and Novelty

```text
test_duplicate_semantic_bug_is_deduplicated
test_same_operator_different_effect_is_distinguished
test_same_family_cross_design_is_recorded_as_transfer
test_family_quota_prevents_archive_monopoly
test_coverage_planner_selects_uncovered_cell
```

## 23.5 Memory Challenge

```text
test_red_cannot_read_memory_source_episode
test_memory_bypass_preserves_surface_effect_but_changes_mechanism
test_memory_deepening_increases_grounded_depth
test_memory_conflict_targets_distinct_definitions
test_memory_transfer_uses_new_design_context
```

## 23.6 Archive and Lineage

```text
test_covered_bug_is_not_deleted
test_residual_bug_replays_across_policy_versions
test_rejected_bug_keeps_rejection_reason
test_lineage_cycle_is_rejected
test_composed_bug_binds_all_parents
test_minimized_poison_preserves_failure_signature
```

---

# 24. 红方核心指标

## 24.1 生成与 Admission

* proposals；
* parser applicability rate；
* elaboration rate；
* compile rate；
* functional-failure rate；
* deterministic validity rate；
* grounded admission yield；
* minimization success rate。

## 24.2 Family 多样性

* admitted family count；
* family coverage；
* family entropy；
* operator coverage；
* runtime-effect coverage；
* role/context coverage；
* design coverage；
* composition coverage。

## 24.3 难度

* frontier bug rate；
* hard-but-repairable rate；
* D0–D4 分布；
* temporal depth；
* dependency depth；
* activation rarity；
* cross-block/module rate。

## 24.4 对抗性

* current-policy effective yield；
* memory bypass yield；
* memory deepening yield；
* memory conflict yield；
* new residual yield；
* old capability forgetting exposure。

## 24.5 对蓝方价值

* generated bug replay recovery；
* prior-failure recovery；
* memory candidate formation rate；
* cross-round memory reuse；
* cross-design memory reuse；
* policy transition gain；
* held-out red generalization；
* non-target regression。

## 24.6 最重要指标

[
\text{Useful Red Yield}
=======================

\frac{
\text{最终形成可验证蓝方能力增量的 bugs}
}{
\text{全部 red proposals}
}
]

可验证能力增量包括：

* frozen replay 恢复；
* 新 memory qualification；
* 后续 round 复用；
* held-out family/design 提升；
* 旧 residual 转为 covered。

---

# 25. 数据划分

动态 red 数据必须区分训练与评价。

## Red-Train Stream

允许：

* policy 写入；
* memory 写入；
* qualification；
* active bank promotion。

## Frozen Red Replay

每轮冻结：

* previously unresolved；
* previously covered；
* memory target；
* non-target。

用于验证：

* 是否吸收旧失败；
* 是否遗忘；
* 是否 harmful；
* 是否需要 revalidation。

## Red-Held-Out

建议采用至少两种隔离：

* held-out designs；
* held-out families；
* held-out operators；
* held-out family compositions；
* held-out difficulty bands。

最终不能只证明：

```text
见过的 poison 被修复
```

而应证明：

```text
红方持续扩大分布后，
蓝方对未参与 memory/policy 写入的新缺陷也获得提升
```

---

# 26. 第一版验收标准

Grounded Red Discovery V1 建议至少满足：

1. false bug admission 为 0；
2. admitted bug 全部具备 clean-pass、poison-fail、revert-pass；
3. admitted bug 全部具有 runner-owned receipts；
4. parser 支持范围内全部具有 semantic-family proof；
5. 初始覆盖不少于 8 个核心 family；
6. 覆盖组合、时序、状态、控制和协议类，而非只有表达式 mutation；
7. 同一 family 不垄断 archive；
8. current-policy-conditioned planner 能产生 frontier 和 hard residual；
9. early-round bugs 可在后续 policy 下 frozen replay；
10. active memory 上线后，红方可生成有效 bypass 或 deepening；
11. 所有生成、拒绝、admission 和 lineage 决策可重建；
12. 红方增强不会绕过 RAAM/Policy Registry authority。

---

# 27. 推荐实施顺序

```text
1. Runner-Owned Receipts
2. Bug Family Ontology
3. Parser-Backed Semantic Diff
4. Artifact-Derived FailureDescriptor
5. Core AST Operators
6. Grounded Admission
7. Coverage-Guided Planner
8. Difficulty Curriculum
9. Memory-Conditioned Red
10. Red Population
11. Controlled Composition
12. Multi-Round Pilot
```

禁止直接从当前状态跳到：

```text
多模型红方混战
+
复杂多点 mutation
+
真实大规模实验
```

否则会同时引入：

* family 混淆；
* validity 噪声；
* 不可解释 hardness；
* 过高验证成本；
* 无法区分 red population 与 operator 的贡献。

---

# 28. 最终研究定位

R³E 后续的红方不应被定义为普通 bug injector，而应定义为：

> 一个根据当前蓝方能力和 active memory 状态，持续探索未覆盖 RTL 功能缺陷空间，并通过 parser、执行工件和硬件 oracle 对每个挑战进行严格验证的对抗式缺陷发现系统。

完整长期闭环为：

```text
Grounded Red Discovery
→ 真实、困难、多样的新 bug
→ Blue repair trajectories
→ Persistent RAAM evidence
→ Shadow qualification
→ Active memory/policy promotion
→ Stronger blue state
→ Memory-conditioned renewed red challenge
→ New family / deeper difficulty / bypass / conflict
```

最终目标不是让红方无限压低蓝方修复率，而是：

> 让红方持续提供处于蓝方能力前沿、具有明确真实性和学习价值的训练压力；让 RAAM 将这些压力转化为可验证、可复用、可跨轮积累的蓝方能力，从而形成 R³E 真正的长期动态进化。
