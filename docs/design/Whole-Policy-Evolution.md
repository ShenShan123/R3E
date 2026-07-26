# R³E 2.0 升级计划书

## Active-Blue-Conditioned Red Search + Whole-Policy Evolution

**工作路径：** `<repo-root>`
**建议开发分支：** `dev/policy-evolution-v2`
**项目定位：** 在现有 R³E 公开仓库上继续升级，实现“红方针对当前 active blue policy 搜索残余失败，蓝方在冻结策略空间中搜索完整 child policy，经冻结重放和非目标回归验证后原子晋升”的闭环系统。

---

# 一、项目目标

## 1. 核心研究目标

构建一个不更新模型权重、但能够发生可验证策略版本变化的 RTL 修复系统：

[
B_t
\rightarrow
R_t(B_t)
\rightarrow
D_t^{\mathrm{residual}}
\rightarrow
\mathcal{S}(B_t,D_t)
\rightarrow
B_{t+1}
\rightarrow
R_{t+1}(B_{t+1})
]

其中：

* (B_t)：formal registry 中唯一有效的当前蓝方策略；
* (R_t(B_t))：显式绑定 (B_t) 的红方搜索；
* (D_t^{\mathrm{residual}})：合法、新颖、当前蓝方难修、但仍可学习的可执行失败；
* (\mathcal{S})：在预先冻结的策略搜索空间内产生 child policies；
* (B_{t+1})：在目标重放上获得增益、且在非目标集合上没有不可接受回归后晋升的整体策略状态。

## 2. 核心研究假设

### H1：当前蓝方条件化红方更容易发现真实弱点

相比只追求合法性、family 多样性或针对旧蓝方的红方，针对当前 active policy 的红方应产生更多：

* 当前策略 0/3 修复失败的 residual；
* 新 failure signature；
* 新 effect-role 组合；
* 当前策略未覆盖、但扩大预算后可学习的毒。

### H2：整体 Policy Evolution 不依赖成功经验的语义迁移

系统不需要证明：

[
\text{成功轨迹}
\rightarrow
\text{通用修复知识}
]

而只需要证明：

[
\text{当前失败分布}
\rightarrow
\text{更合适的整体修复策略配置}
]

### H3：renewed challenge 可以区分真实进化与固定集合过拟合

真正的动态进化不能只表现为：

[
B_0,B_1,B_2
]

在同一批固定毒上的修复率逐渐提高。

还需要表现为：

[
R_0(B_0)
\rightarrow B_1
\rightarrow R_1(B_1)
\rightarrow B_2
]

即 B1 吸收旧失败后，红方重新攻击 B1，并找到新的 residual。

### H4：同一 family 内仍然存在可持续演化空间

同一 `off_by_one` family 内可能包含：

* counter terminal condition；
* bit slice boundary；
* pipeline latency；
* FSM transition offset；
* array index；
* valid/data 时序错位。

因此 family 只能作为粗标签，不能作为“一旦已有晋升项就不再更新”的唯一键。

---

# 二、本阶段明确不做的内容

为控制工程量和实验变量，本阶段主动排除：

1. 不实现完整可执行 RTL Skill IR。
2. 不从成功 patch 中自动学习 AST rewrite 规则。
3. 不把自由文本 repair rationale 直接晋升成 runtime memory。
4. 不允许人工在实验过程中新增 family-specific hint。
5. 不更新红蓝模型权重。
6. 不立即开展全量多红×多蓝混战。
7. 不以 best-so-far 曲线代替真实 active policy 状态。
8. 不将 formal timeout 或 incomplete proof 当作已证明不等价。
9. 不同时扩展前端语义修复、后端 WNS 闭合和跨模块修复。
10. 不把更多 LLM 调用自动解释为策略进化。

---

# 三、系统中的四类权威对象

## 1. Frozen Base Policy

Frozen Base Policy 可以人工设计，但必须在实验开始前冻结。

它可以包含：

* 基础修复 prompt；
* 一组预先声明的 prompt lens；
* mismatch evidence 提取器；
* candidate 生成接口；
* candidate 选择方式；
* verifier；
* 可搜索的 policy mutation operators；
* 允许使用的模型和预算；
* 工具链配置。

建议新增：

```text
configs/base_policy/
├── frozen_base_policy_v1.json
├── policy_search_space_v1.json
└── prompt_templates/
    ├── generic_v1.txt
    ├── control_v1.txt
    ├── dataflow_v1.txt
    └── temporal_v1.txt
```

Frozen Base Policy 是 (B_0) 的基础，但不能冒充自动学习经验。

## 2. Active Policy State

Active Policy State 是当前真正执行的蓝方策略。

任何正式运行只能从 formal registry 中读取一个 active policy，不允许：

* 同时加载多个相互独立 registry；
* 从 `configs/skills.json` 私自补充 promoted skill；
* 在运行时临时修改 prompt；
* 根据 target 结果人工调整策略。

## 3. Red Residual Archive

保存针对特定蓝方策略发现的红方失败，包括：

* poison 内容；
* challenged policy hash；
* 蓝方修复成功次数；
* hardness；
* novelty；
* learnability；
* family/effect/role；
* failure signature；
* lineage；
* oracle 证据；
* 文件哈希。

## 4. Formal Policy Registry

Formal Policy Registry 是唯一能够改变 active policy 的权威。

手工 skills 只能属于：

```text
Frozen Base Policy
```

不能带有自动晋升语义。

---

# 四、总体闭环流程

每轮执行以下步骤。

## Round (t)

1. 从 formal registry 加载唯一 active policy (B_t)。
2. 红方读取 (B_t) 的能力摘要和失败历史。
3. 红方生成候选 poison。
4. validity gate 过滤无效毒。
5. 使用 (B_t) 的固定预算和多 seed 修复合法毒。
6. 估计每条毒对当前蓝方的 hardness。
7. 根据 novelty 和 learnability 更新 residual archive。
8. 冻结本轮 residual manifest。
9. 将 residual 分成 adaptation pool 与 target replay pool。
10. 在冻结 policy search space 中生成 child policies。
11. 在 adaptation pool 上做低成本 screening。
12. parent 和 surviving child 在 target/non-target 上做 paired replay。
13. promotion gate 决定是否晋升。
14. registry 原子切换到 (B_{t+1})。
15. 下一轮红方必须绑定 (B_{t+1})。
16. 若后续审计发现回归，回滚到父 policy。

完整流程为：

[
\boxed{
B_t
\rightarrow
\text{Conditioned Red Search}
\rightarrow
\text{Residual Archive}
\rightarrow
\text{Child Policy Search}
\rightarrow
\text{Frozen Paired Replay}
\rightarrow
B_{t+1}
}
]

---

# 五、红方强化设计

## 1. 红方输入升级

当前红方主要读取 golden RTL 和简单 `red_ctx`。下一版需要引入：

## Blue Capability Packet

示例：

```json
{
  "challenged_policy_id": "B1",
  "challenged_policy_hash": "sha256:...",
  "design_id": "design_x",
  "golden_rtl_hash": "sha256:...",
  "allowed_mutation_operators": [
    "constant_error",
    "off_by_one",
    "condition_error",
    "dataflow_error",
    "state_swap_error"
  ],
  "covered_archive_cells": [
    "off_by_one|counter_terminal|control|same_cycle"
  ],
  "recent_easy_variants": [
    "single literal replacement on output assignment"
  ],
  "current_failure_summary": {
    "repair_attempts": 3,
    "repair_successes": 0,
    "failure_stages": ["compare", "compare", "compare"],
    "first_divergence_signals": ["out_valid", "state"],
    "first_divergence_cycles": [4, 4, 5],
    "blue_patch_scope_histogram": {
      "expression": 2,
      "local_block": 1
    }
  },
  "search_objective": {
    "prefer_uncovered_effects": true,
    "prefer_uncovered_roles": true,
    "max_composition_depth": 2
  }
}
```

红方可以看到：

* 当前蓝方策略 hash；
* 当前策略已经覆盖哪些 poison；
* 哪些毒会被轻易修复；
* 蓝方失败发生在哪些信号和周期；
* 当前 archive 中哪些区域尚未覆盖。

但红方不能看到：

* target replay 的隐藏 oracle 标签；
* 人工参考修复说明；
* 后续 child policy 的验证结果；
* 未公开的人工规则。

---

## 2. 两类红方生成路径

### Path A：Fresh Mutation

从 golden RTL 生成新的合法毒，用于探索：

* 新 family；
* 新 effect；
* 新 affected role；
* 新 temporal depth；
* 新 edit scope。

### Path B：Lineage Deepening

从已有合法毒继续演化。

允许的 lineage operator：

#### Deepen

增加错误所依赖的控制或状态关系。

```text
单个边界常量错误
→ 边界条件与状态更新耦合
```

#### Relocate

将同一错误类型移动到不同 role。

```text
数据路径 off-by-one
→ valid-control off-by-one
```

#### Temporalize

让错误不在当前周期立即出现，而在后续周期暴露。

```text
same-cycle mismatch
→ one-cycle lag
→ multi-cycle accumulated mismatch
```

#### Compose

组合两个允许的轻量变异。

```text
off-by-one + condition flip
```

前期限定：

```text
composition_depth <= 2
changed_modules <= 1
changed_blocks <= 1
```

#### Counterexample-Guided Revise

根据蓝方为什么轻易修好当前毒，生成更难但仍然单义的变体。

例如蓝方始终通过搜索常量定位错误，则红方可以：

* 将错误从字面量迁移到比较边界；
* 将同类错误迁移到状态转移条件；
* 保持 family 不变，但改变 effect 和 role。

---

## 3. 红方合法性硬门

一条 poison 必须同时满足：

1. golden compile PASS；
2. golden simulation/formal PASS；
3. buggy compile PASS；
4. buggy 确定功能 FAIL；
5. buggy 与 golden 文件 hash 不同；
6. 输出结构完整；
7. 没有修改 testbench；
8. 没有修改 timing、SDC 或工具配置；
9. diff 在允许的 scope 内；
10. formal 结果不是 timeout、unknown 或 inconclusive；
11. 输出不是上一次运行遗留文件；
12. 回退到 golden 可通过验证。

Validity 是硬门，不应仅作为 fitness 中的一个低权重分量。

---

## 4. 当前蓝方难度估计

对每条合法毒，在固定 (B_t) 和固定预算下运行 (m) 次：

[
\hat p_{\mathrm{repair}}(r,B_t)
===============================

\frac{s}{m}
]

MVP 建议：

```text
m = 3 seeds
temperature 固定
candidate budget 固定
verifier 固定
timeout 固定
```

定义 hardness：

[
H(r,B_t)
========

1-\hat p_{\mathrm{repair}}(r,B_t)
]

分档：

| 修复结果 | 分类                  |
| ---- | ------------------- |
| 0/3  | hard residual       |
| 1/3  | borderline residual |
| 2/3  | mostly covered      |
| 3/3  | covered             |

主要 residual adaptation pool 优先使用 0/3。

1/3 可以作为压力样本，2/3 和 3/3 进入 covered archive，避免红方反复生成当前蓝方已经掌握的简单毒。

---

## 5. Novelty Descriptor

每条 poison 记录：

```text
family
effect
affected_role
edit_scope
composition_depth
sequential_depth
first_divergence_signal
first_divergence_cycle_bucket
failure_signature
normalized_diff_hash
dataflow_cone_hash
challenged_policy_hash
```

建议使用 MAP-Elites 风格 archive cell：

[
(
\text{family},
\text{effect},
\text{role},
\text{temporal bucket},
\text{scope}
)
]

同一 cell 可保留：

* hardness 最高样本；
* edit 最小样本；
* learnability 最好样本。

避免仅按 `mutation_type` 文本去重。

---

## 6. Learnability

红方不能只追求制造当前蓝方永远修不好的毒。

定义 teacher/expanded-budget probe：

* 当前 (B_t) 固定预算失败；
* 扩大预算、使用更强模型或更强蓝方 population 后至少一次修复成功。

建议标签：

```text
reachable
weakly_reachable
unknown
unlearnable_or_budget_exceeded
```

其中：

* `reachable`：相同模型扩大预算可以修复；
* `weakly_reachable`：更强模型或 population 可以修复；
* `unknown`：尚无成功，但合法性明确；
* `unlearnable_or_budget_exceeded`：多次扩大搜索仍失败。

主要 adaptation residual 应优先使用前两类。

---

## 7. 红方优化目标

第一版不建议直接训练强化学习模型，而应先实现可解释的多目标搜索。

优先级：

1. validity；
2. current-blue hardness；
3. novelty；
4. learnability；
5. 修改规模和运行成本。

推荐采用 Pareto frontier。

需要标量化时可使用：

[
F(r;B_t)
========

0.45H
+
0.30N
+
0.20L
-----

0.05C
]

但论文中必须分别报告 hardness、novelty、learnability，不能只报告总分。

---

# 六、同一 family 的持续进化

family 只作为一级标签：

```text
family
└── effect
    └── affected_role
        └── temporal_depth
            └── edit_scope
                └── variant_lineage
```

示例：

```text
off_by_one
├── counter_terminal / control / same-cycle / expression
├── counter_terminal / state / one-cycle / block
├── bit_slice / datapath / same-cycle / expression
├── pipeline_latency / output / one-cycle / block
└── pipeline_latency / valid-control / multi-cycle / block
```

每条 poison 记录 lineage：

```json
{
  "poison_id": "R1-P023",
  "parent_poison_id": "R0-P008",
  "lineage_depth": 2,
  "family": "off_by_one",
  "effect": "pipeline_latency",
  "affected_role": "valid_control",
  "evolution_operator": "temporalize"
}
```

删除任何类似以下逻辑：

```text
family 已存在 promoted entry
→ 跳过后续 family candidate
```

同一 family 可以拥有多个不同 archive cell，也可以在蓝方策略升级后继续生成更深变体。

---

# 七、整体 Policy Evolution

## 1. Policy State Schema

建议完整 Policy State：

```json
{
  "policy_id": "B2",
  "schema_version": "r3e-policy-v2",
  "parent_policy_id": "B1",
  "parent_policy_hash": "sha256:...",
  "base_policy_hash": "sha256:...",
  "created_round": 2,
  "created_from_residual_manifest_hash": "sha256:...",
  "configuration": {
    "evidence_mode": "hybrid",
    "evidence_k": 6,
    "n_candidates": 3,
    "repair_loop": "critique_revise",
    "prompt_lens_id": "temporal_v1",
    "candidate_selection": "first_verified",
    "model_route_id": "single_default",
    "blue_population": 1,
    "patch_scope": "local_block",
    "verifier_order": ["simulation"]
  },
  "budgets": {
    "max_llm_calls_per_case": 6,
    "max_wall_seconds_per_case": 360,
    "max_tokens_per_case": 48000
  },
  "status": "candidate",
  "configuration_hash": "sha256:...",
  "validation_manifest_hash": "sha256:...",
  "promotion_decision_hash": "",
  "rollback_policy_id": "B1"
}
```

## 2. 第一阶段搜索空间

MVP 只开放四个维度：

| 维度            | 候选                         |
| ------------- | -------------------------- |
| evidence mode | raw / hybrid               |
| evidence_k    | 1 / 3 / 6                  |
| n_candidates  | 1 / 3 / 6                  |
| repair loop   | one-shot / critique-revise |

总组合数为：

[
2\times3\times3\times2=36
]

但每轮只搜索 parent 的局部邻域，不全量运行。

## 3. 第二阶段可扩展维度

在最小闭环稳定后，再增加：

* prompt lens：generic/control/dataflow/temporal；
* candidate selection：first-verified/critic-ranked/verifier-guided；
* model route：single/strong-on-hard/heterogeneous；
* blue population：1/3/5；
* verifier order；
* patch scope。

## 4. 不允许进入搜索空间的内容

* 未冻结的自由文本 hint；
* 直接拼接成功轨迹；
* 运行后人工新增的 family rule；
* 根据 target replay 结果重新修改 candidate；
* 不受预算约束的更多调用；
* 自动更换 verifier 判据。

---

## 5. Child Policy 生成

每轮从 parent 生成三类 child：

### One-Factor Neighbor

一次只修改一个维度。

```text
B0:
raw + k1 + n1 + one-shot

B0-child-1:
hybrid + k1 + n1 + one-shot
```

### Residual-Conditioned Child

依据 adaptation residual 统计选择预定义 operator。

例如：

* 多周期 mismatch 集中：提高 `evidence_k`；
* 蓝方经常给出接近正确但不完整的 patch：启用 critique-revise；
* 多次候选差异较大：增加 `n_candidates`；
* dataflow residual 集中：切换 frozen dataflow lens。

### Exploration Child

从尚未评估的合法组合中随机选取。

MVP 每轮建议：

```text
K_child = 6

3 个 one-factor
2 个 residual-conditioned
1 个 exploration
```

---

## 6. 两阶段 Policy Search

### Stage 1：Adaptation Screening

在 adaptation pool 上低成本筛选。

目标是淘汰：

* 明显弱于 parent；
* 成本过高；
* 只对单个 case 生效；
* 大范围破坏非目标能力的 child。

### Stage 2：Frozen Promotion Replay

通过 screening 的 child 进入正式 target/non-target paired replay。

一旦 target manifest 冻结：

* child 配置必须冻结；
* target 结果不能反馈给 child search；
* 失败 child 只能在下一轮重新提出新版本；
* 不能在同一 promotion trial 中反复试到通过。

---

# 八、Formal Registry V2

## 1. 单一权威原则

正式 runtime 只读取：

```text
runtime/registry/policy_registry.json
```

现有 `configs/skills.json` 应：

* 改名为 `configs/legacy/manual_skills.json`；或
* 转为 `configs/base_policy/frozen_base_policy_v1.json`；
* 删除 `status: promoted` 的权威含义；
* formal mode 加载 manual/legacy promoted entry 时 fail closed。

## 2. Registry 结构

```json
{
  "schema_version": "r3e-policy-registry-v2",
  "base_policy": {
    "policy_id": "B0",
    "path": "configs/base_policy/frozen_base_policy_v1.json",
    "hash": "sha256:..."
  },
  "active_policy_id": "B1",
  "policies": {
    "B0": {
      "status": "superseded"
    },
    "B1": {
      "status": "active"
    },
    "B2_candidate_01": {
      "status": "rejected"
    }
  },
  "registry_parent_hash": "sha256:...",
  "registry_hash": "sha256:..."
}
```

任意时间只能存在一个 `active` policy。

## 3. Policy DAG 操作

支持：

```text
create-child
promote
reject
supersede
rollback
retire
audit-fail
```

每个 child 必须绑定当前 active parent。

若 parent 已经变化，则该 child 成为 stale candidate，不得晋升。

## 4. Decision Ledger

每条决策记录：

```text
round_id
parent_policy_id/hash
candidate_policy_id/hash
residual_manifest_hash
adaptation_manifest_hash
target_manifest_hash
non_target_manifest_hash
paired_result_hash
thresholds
decision
rejection_reasons
registry_hash_before
registry_hash_after
code_commit_sha
toolchain_fingerprint
timestamp
```

---

# 九、Promotion Protocol

## 1. 三类数据

### Adaptation Pool (A_t)

允许用于：

* child proposal；
* child screening；
* residual-conditioned operator 选择。

### Frozen Target Replay (T_t)

用于正式 promotion，不能用于 child 配置搜索。

优先采用 design-level 隔离：

```text
同一 design 不能同时进入 adaptation 和 target
```

样本不足时使用：

* leave-one-design-out；
* repeated grouped split；
* 多轮 residual 累积后再 promotion。

### Non-Target Regression Pool (N_t)

至少包含：

* CirFix-39；
* 以前轮次已经掌握的 residual；
* 与 target design-disjoint 的冻结 red set；
* 必要时增加 Literature-32 或 Strider-14 监控子集。

---

## 2. 成对评估要求

parent 与 candidate 必须使用相同：

* case；
* seed；
  -模型；
* 总预算上限；
* verifier；
* timeout；
* toolchain；
* target/non-target manifest。

任何差异都需要写入实验变量，不能隐藏。

---

## 3. 晋升判据

第一版建议：

```text
target_gain > 0
prior_failure_recovery >= 2
target_recovery_ratio >= 0.50
covered_designs >= 2
non_target_delta >= -epsilon
critical_regressions == 0
candidate_cost <= 1.5 * parent_cost
provenance/hash/rollback checks 全部通过
```

建议：

```text
epsilon = 0.02
```

当样本较少时，同时报告：

* W/L/T；
* prior-failure recovery；
* exact McNemar；
* Wilson CI；
* 多 seed mean±sample SD；
* per-case paired outcome。

## 4. 三类决策

### Strong Promotion

目标增益达到阈值，非目标无不可接受回归。

可以成为新的 active policy。

### Provisional Candidate

方向为正，但样本量或覆盖不足。

只能继续留在 shadow evaluation，不成为 active。

### Reject

包括：

* 无目标增益；
* 回归；
* 成本超限；
* provenance 不完整；
* target 泄漏；
* stale parent；
* hash 不可重建。

正式 active policy 只接受 Strong Promotion。

---

# 十、Round State Machine

实现可恢复状态机：

```text
INIT
→ LOAD_ACTIVE_POLICY
→ RED_GENERATE
→ VALIDITY_GATE
→ BLUE_CHALLENGE
→ ARCHIVE_UPDATE
→ FREEZE_RESIDUAL_MANIFEST
→ SPLIT_ADAPT_TARGET
→ PROPOSE_CHILDREN
→ SCREEN_CHILDREN
→ FREEZE_PROMOTION_MANIFEST
→ PAIRED_REPLAY
→ DECIDE
→ ATOMIC_COMMIT
→ RENEWED_CHALLENGE
→ COMPLETE
```

每个阶段写入：

```json
{
  "stage": "BLUE_CHALLENGE",
  "stage_input_hash": "...",
  "stage_output_hash": "...",
  "started_at": "...",
  "completed_at": "...",
  "status": "complete",
  "failure_reason": null
}
```

系统被终止后，只能从最后一个完整的 hash-bound checkpoint 继续。

---

# 十一、核心伪代码

```python
def run_round(round_cfg):
    registry = load_policy_registry(formal_mode=True)
    parent = registry.get_active_policy()

    assert parent.hash == round_cfg.expected_parent_policy_hash

    red_candidates = red_search.generate(
        policy=parent,
        designs=round_cfg.red_design_pool,
        archive=load_red_archive(),
    )

    valid_poisons = [
        poison
        for poison in red_candidates
        if validity_gate(poison).proven_valid
    ]

    challenged = []

    for poison in valid_poisons:
        result = evaluate_blue_policy(
            policy=parent,
            poison=poison,
            seeds=round_cfg.challenge_seeds,
            fixed_budget=round_cfg.blue_budget,
        )

        challenged.append(
            bind_blue_outcome(
                poison=poison,
                policy=parent,
                result=result,
            )
        )

    residuals = select_residuals(
        challenged,
        hardness_threshold=round_cfg.hardness_threshold,
        novelty_archive=load_red_archive(),
        learnability_probe=round_cfg.learnability_probe,
    )

    residual_manifest = freeze_manifest(residuals)

    adaptation, target = grouped_split(
        residual_manifest,
        group_key="design",
    )

    children = policy_search.propose_children(
        parent=parent,
        adaptation_residuals=adaptation,
        frozen_operator_space=round_cfg.policy_operator_space,
    )

    screened = screen_on_adaptation(
        parent=parent,
        children=children,
        adaptation=adaptation,
    )

    decisions = []

    for child in screened:
        validation_rows = paired_replay(
            parent=parent,
            candidate=child,
            target=target,
            non_target=round_cfg.non_target_manifest,
            seeds=round_cfg.promotion_seeds,
        )

        decision = decide_policy_promotion(
            parent=parent,
            candidate=child,
            validation_rows=validation_rows,
            thresholds=round_cfg.promotion_thresholds,
        )

        decisions.append(decision)

    winner = select_single_promotable_child(decisions)

    if winner:
        atomic_promote_policy(
            registry=registry,
            candidate=winner.policy,
            decision=winner.decision,
        )
        next_policy = winner.policy
    else:
        next_policy = parent

    write_round_ledger(...)

    return next_policy
```

---

# 十二、代码重构方案

## 1. 新增目录

```text
r3e/
├── policy/
│   ├── schema.py
│   ├── state.py
│   ├── operators.py
│   ├── search.py
│   ├── registry_v2.py
│   ├── promotion.py
│   └── runtime.py
├── red/
│   ├── generator.py
│   ├── feedback_packet.py
│   ├── validity.py
│   ├── challenge.py
│   ├── fitness.py
│   ├── novelty.py
│   ├── archive.py
│   └── lineage.py
├── arena/
│   ├── round_state.py
│   ├── manifests.py
│   ├── paired_replay.py
│   ├── renewed_challenge.py
│   └── runner.py
└── protocol/
    ├── hashing.py
    ├── ledger.py
    ├── rollback.py
    └── toolchain_fingerprint.py
```

---

## 2. `red_mutator.py`

保留兼容入口，但内部迁移到：

```text
r3e/red/generator.py
r3e/red/feedback_packet.py
r3e/red/validity.py
```

建议新接口：

```python
generate_poison(
    case,
    challenged_policy,
    blue_failure_summary,
    archive_summary,
    parent_poison=None,
)
```

---

## 3. `functional_repair.py`

当前分散传入：

```text
evidence_k
n_candidates
structured_evidence
preflight_registry
recall_fn
```

应改成：

```python
repair_one(
    case,
    work_dir,
    policy: PolicyState,
)
```

formal path 中：

* 禁止任意 `recall_fn` 文本注入；
* 所有 prompt lens 必须来自 frozen base policy；
* 每条结果记录 effective policy hash；
* 所有预算由 PolicyState 统一控制。

legacy path 可暂时保留，但必须显式标注：

```text
formal_mode=False
```

---

## 4. `formal_protocol.py`

保留已有的：

* canonical hashing；
* atomic write；
* writer lock；
* paired validation；
* stale-parent rejection；
* rollback；
* decision ledger。

替换的数据模型：

```text
candidate strategy artifact
→ candidate whole policy
```

修改：

```text
append promoted artifacts
→ 将唯一 active policy 切换为 child
```

---

## 5. `skill_registry.py` 与 `skill_preflight.py`

处理方式：

* 移出 formal runtime；
* 仅作为 legacy/frozen base adapter；
* formal mode 检测到 manual promoted skill 时 fail closed；
* 不再把 skill family label 作为自动晋升证据；
* 不再限制同一 family 只能存在一个版本。

---

## 6. `correctness_gated_accumulation_curve.py`

不要继续向单个两千多行脚本叠加逻辑。

建议迁移到：

```text
experiments/legacy/
```

并拆分：

* round orchestration；
* red search；
* policy search；
* promotion；
* replay；
* aggregation。

---

# 十三、必须先修复的 Oracle P0 问题

任何新实验开始前必须完成：

1. 每一行输出列数必须与 header 完全一致。
2. candidate 多余输出 cycle 也必须拒绝。
3. 检查 `vvp` return code。
4. 防止不同依赖文件 basename 相同时互相覆盖。
5. 防止读取前一次运行遗留的输出文件。
6. formal gate 明确区分：

   * `PROVEN_EQUIV`
   * `PROVEN_NON_EQUIV`
   * `INCONCLUSIVE`
   * `TIMEOUT`
   * `TOOL_ERROR`
7. 只有 `PROVEN_NON_EQUIV` 才能作为 formal poison。
8. 默认采用 design-disjoint split。
9. 每个 oracle 结果记录工具版本与命令 hash。
10. golden 和 candidate 输出行、列、周期长度均必须严格一致。

---

# 十四、运行目录设计

公开代码和运行产物分离：

```text
<repo-root>/
├── r3e/
├── configs/
├── datasets/
├── experiments/
├── tests/
├── scripts/
├── runtime/             # gitignored
│   ├── registry/
│   ├── archives/
│   ├── rounds/
│   └── caches/
└── results/             # gitignored 或仅提交聚合制品
```

每轮目录：

```text
runtime/rounds/R000/
├── round_config.json
├── round_state.json
├── toolchain.json
├── active_parent.json
├── red_candidates.jsonl
├── valid_poisons.jsonl
├── blue_challenge_results.jsonl
├── residual_manifest.json
├── adaptation_manifest.json
├── target_manifest.json
├── child_policies.jsonl
├── screening_results.jsonl
├── paired_validation.jsonl
├── promotion_decisions.jsonl
├── registry_before.json
├── registry_after.json
└── round_summary.json
```

---

# 十五、CLI 设计

```bash
# 初始化 frozen base policy 与 registry
python -m r3e.policy.registry_v2 init \
  --base configs/base_policy/frozen_base_policy_v1.json \
  --registry runtime/registry/policy_registry.json

# 执行一轮闭环
python -m r3e.arena.runner \
  --config configs/evolution/round_v1.yaml \
  --round-id R000

# 只运行红方挑战
python -m r3e.red.challenge \
  --policy-registry runtime/registry/policy_registry.json \
  --manifest datasets/... \
  --out runtime/rounds/R000

# 产生 child policies
python -m r3e.policy.search \
  --parent-policy runtime/rounds/R000/active_parent.json \
  --adaptation-manifest runtime/rounds/R000/adaptation_manifest.json \
  --search-space configs/base_policy/policy_search_space_v1.json

# paired replay
python -m r3e.arena.paired_replay \
  --parent ... \
  --candidate ... \
  --target ... \
  --non-target ...

# promotion
python -m r3e.policy.promotion decide ...
python -m r3e.policy.registry_v2 commit ...

# renewed challenge
python -m r3e.arena.renewed_challenge \
  --expected-policy-hash <B1_HASH> \
  --round-id R001
```

---

# 十六、实施阶段

## Phase 0：基线冻结与安全修复

任务：

* 创建开发分支；
* 给当前版本打 legacy tag；
* 修复 oracle P0；
* formal status 枚举化；
* 建立 toolchain fingerprint；
* 降级 `configs/skills.json`；
* 建立 frozen base policy；
* 保存当前结果的 legacy reproduction 配置。

验收：

* 旧 offline tests 通过；
* 新 oracle adversarial tests 通过；
* formal mode 无法加载 manual promoted skill；
* 多次初始化得到相同 B0 hash。

---

## Phase 1：Policy Registry V2

任务：

* 实现 PolicyState schema；
* 单 active policy；
* parent-child lineage；
* atomic promotion；
* stale parent rejection；
* rollback；
* decision ledger；
* registry migration。

验收：

* 不能同时存在两个 active policies；
* 修改任一配置字段均改变 policy hash；
* stale candidate 无法晋升；
* rollback 精确恢复父版本；
* 异常中断不会生成半写 registry。

---

## Phase 2：Repair Runtime Policy 化

任务：

* `repair_one(..., policy)`；
* 冻结 prompt templates；
* 统一预算计数；
* 记录 token/call/time；
* parent/candidate paired execution；
* formal path 禁止自由 memory 注入。

验收：

* 每条结果都包含 policy hash；
* 相同 policy、seed、case 可重建配置；
* 预算超限 fail closed；
* parent/candidate 运行元数据可成对核对。

---

## Phase 3：Active-Blue-Conditioned Red

任务：

* Blue Capability Packet；
* challenged-policy binding；
* 3-seed hardness；
* novelty archive；
* lineage deepening；
* same-family 多 cell；
* learnability probe；
* residual manifest freeze。

验收：

* 每条 poison 都绑定 challenged policy hash；
* 第二轮 poison 绑定晋升后的 B1；
* 同一 family 可以进入多个 effect-role cell；
* invalid/inconclusive poison 不能进入 archive；
* 当前蓝方已掌握的毒不会占用主要 residual 配额。

---

## Phase 4：Policy Search

任务：

* 冻结 operator space；
* one-factor neighbors；
* residual-conditioned operators；
* adaptation screening；
* target holdout；
* winner selection。

验收：

* child 不含运行后人工添加字段；
* target 不参与 child 配置选择；
* 每轮最多晋升一个 child；
* 没有 child 通过时保持 parent。

---

## Phase 5：两轮最小闭环

必须跑通：

[
B_0
\rightarrow
R_0(B_0)
\rightarrow
B_1
\rightarrow
R_1(B_1)
\rightarrow
B_2
]

验收：

1. B1 和 B2 均由自动 policy search 产生。
2. B1 在 B0 residual target 上获得正增益。
3. B1 non-target 无不可接受回归。
4. R1 明确绑定 B1 policy hash。
5. R1 至少找到一个新的 residual archive cell。
6. B2 恢复部分 B1 residual。
7. 三个 seed 的方向一致。
8. 增益不能完全由更多预算解释。

---

## Phase 6：完整实验与公开复现

任务：

* 冻结最终 configs；
* 固定 seeds；
* 自动聚合；
* 生成表格和图；
* 更新 README；
* 加入 sanitized runner；
* 更新 release checker；
* 建立公开 artifact manifest。

验收：

* 从空 runtime 目录可以重建一个小规模闭环；
* 每个数字都可追溯到 JSONL；
* 图表可由脚本重建；
* 仓库不含凭证、私有路径或原始模型隐私输出。

---

# 十七、测试计划

## 1. Unit Tests

* PolicyState schema；
* policy hash stability；
* operator legality；
* registry single-active；
* stale parent rejection；
* atomic commit；
* rollback；
* decision reconstruction；
* archive cell assignment；
* novelty dedup；
* hardness；
* learnability；
* residual manifest immutability；
* grouped split no-overlap；
* budget accounting。

## 2. Oracle Adversarial Tests

必须覆盖：

* candidate 少一列；
* candidate 多一列；
* 少一个周期；
* 多一个周期；
* vvp 非零但存在旧输出；
* basename 冲突；
* 空输出；
* header 相同但列顺序错误；
* formal timeout；
* formal incomplete proof；
* golden 与 candidate 完全相同。

## 3. Integration Tests

* B0 加载→red candidate→validity→challenge；
* residual→child→paired replay→reject；
* residual→child→promote；
* promote 后 renewed challenge 使用新 policy hash；
* 中途 kill 后恢复；
* 并发 registry writer；
* later audit rollback。

## 4. Leakage Tests

* target case 不进入 adaptation；
* target oracle label 不进入 prompt；
* child 冻结后不能修改；
* manual skills 不能进入 formal registry；
* 运行结果不能改变 frozen base policy；
* 同 design 分组隔离。

---

# 十八、实验设计

## RQ1：当前蓝方条件化红方是否更有效？

对比：

1. Random legal red；
2. Family-balanced red；
3. Old-blue-conditioned red；
4. Active-blue-conditioned red；
5. Active-blue + novelty；
6. Active-blue + novelty + learnability。

指标：

* valid poison rate；
* 0/3 residual rate；
* unique archive cells；
* family/effect/role diversity；
* duplicate rate；
* teacher-reachable rate；
* 每条 residual 的 LLM 和时间成本。

最关键对比：

```text
Old-blue-conditioned
vs
Active-blue-conditioned
```

---

## RQ2：整体 Policy Evolution 是否恢复父策略旧失败？

对比：

* Frozen B0；
* Random child；
* Budget-only child；
* Residual-conditioned child；
* Full whole-policy evolution。

指标：

* target replay gain；
* prior-failure recovery；
* non-target delta；
* promotion acceptance rate；
* cost delta；
* 多 seed transition 一致性。

---

## RQ3：renewed challenge 是否发现新残余能力？

比较：

* R0 对 B0；
* 固定 R0 poison 对 B1；
* R1 重新攻击 B1。

需要证明：

1. B1 对 R0 residual 有提高；
2. R1(B1) 能发现新失败；
3. R1 不是简单重复 R0 archive cell；
4. B2 对 R1 residual 再次提高。

---

## RQ4：同一 family 是否可以持续进化？

建议选择：

* off-by-one；
* constant error；
* condition error；
* dataflow 或 state 类至少一种。

报告层级：

```text
family
→ effect
→ role
→ temporal depth
→ scope
```

指标：

* 同 family unique cells；
* lineage depth；
* child poison 对新 active policy 的 hardness；
* composition depth=1/2 的合法率和 learnability。

---

## RQ5：formal registry 是否真正提供唯一权威？

消融：

* manual skills allowed；
* multiple registries；
* no parent hash；
* no frozen target；
* no non-target regression；
* full registry v2。

指标：

* false promotion；
* stale promotion；
* regression；
* non-reconstructable decision；
* active policy ambiguity；
* rollback success。

---

# 十九、实验口径

## 数据集角色

* CirFix-39：固定公共修复能力与 non-target 监控；
* Red-Fixed-12：历史机制比较；
* Held-out Red-24：迁移压力；
* 新生成：

  * `R0-B0-Residual`
  * `R1-B1-Residual`
  * `R2-B2-Residual`
* design-held-out：adaptation/target 隔离。

## Seeds

* 开发阶段：3 seeds；
* 最终核心结果：5 seeds；
* 红方生成 seed 与蓝方修复 seed 分开；
* paired replay 中 parent 与 child 使用相同 seeds。

## 预算

主口径建议：

```text
n_candidates <= 3
evidence_k <= 6
最多一轮 critique
统一每 case LLM 调用上限
统一 token 和时间上限
```

扩大预算只用于 learnability teacher，不与主蓝方混为同一方法。

---

# 二十、建议结果表和图

## Table 1：Red Search Quality

```text
Method
Valid
0/3 Residual
Unique Cells
Reachable
Duplicate Rate
Cost
```

## Table 2：Policy Transition

```text
Transition
Parent Target
Child Target
Recovery
Non-target Δ
Cost Δ
Decision
```

## Table 3：Renewed Challenge

```text
Red Round
Challenged Policy
Valid Poison
New Cells
Residual
Repeated Cells
```

## Figure 1：闭环系统结构

```text
Active Policy
→ Conditioned Red Search
→ Valid/Novel/Learnable Residuals
→ Child Policy Search
→ Frozen Paired Replay
→ Atomic Promotion
→ Renewed Challenge
```

## Figure 2：真实 Policy States

只画：

```text
B0 → B1 → B2
```

不要画 best-so-far 替代 active state。

## Figure 3：Residual Archive Expansion

展示每轮：

* family；
* effect；
* role；
* temporal cell 增长。

## Figure 4：Parent-Child Paired Outcomes

使用：

* W/L/T；
* paired heatmap；
* prior-failure recovery。

---

# 二十一、风险与止损

## 风险 1：没有 child 能通过晋升

处理：

* 保持 parent；
* 如实报告负结果；
* 检查 frozen operator space 是否过窄；
* 只能在下一实验版本扩展冻结搜索空间；
* 不允许人工临时加入 hint。

## 风险 2：红方只生成不可学习毒

处理：

* learnability gate；
* 限制 composition 和 scope；
* teacher probe；
* unlearnable poison 单独报告，不进入主要 adaptation。

## 风险 3：target 太小

处理：

* 多轮积累后再 promotion；
* grouped cross-validation；
* exact paired evidence；
* 不夸大统计显著性。

## 风险 4：child 只是预算更高

处理：

* 预算匹配；
* 加入 budget-only baseline；
* 报告 cost-normalized gain；
* 限制 child 总调用上限。

## 风险 5：同 family 演化退化成多 bug 堆叠

处理：

* composition depth 上限；
* 单 block 限制；
* normalized diff audit；
* 单毒和组合毒分表；
* 每个 effect 必须可解释。

## 风险 6：公开仓库无法包含 runner

现有 release policy 应调整为：

* 允许 sanitized reproducible runners；
* 继续禁止凭证、私有路径和生成结果；
* 私有集群 launcher 放在 gitignored 的 `private_launch/`；
* README 解释公共 runner 与私有调度脚本的边界。

---

# 二十二、Git 工作流

建议执行：

```bash
cd <repo-root>

git status
git fetch origin
git checkout main
git pull --ff-only

git tag legacy-r3e-artifact-20260726
git checkout -b dev/policy-evolution-v2
```

建议拆成以下 PR：

1. `fix/oracle-fail-closed`
2. `feat/policy-registry-v2`
3. `refactor/policy-driven-repair-runtime`
4. `feat/active-blue-red-search`
5. `feat/policy-search-and-promotion`
6. `feat/renewed-challenge-runner`
7. `exp/two-round-closed-loop`
8. `docs/reproducible-r3e-v2`

每个 PR 必须包含：

* 单元测试；
* schema 变更说明；
* backward compatibility 说明；
* 不包含运行结果和密钥；
* 关键 hash 和 manifest 示例。

---

# 二十三、第一批立即执行任务

## P0：基础可信性

* [ ] 创建开发分支与 legacy tag。
* [ ] 修复 oracle 输出列数。
* [ ] 拒绝额外 candidate cycles。
* [ ] 检查 vvp return code。
* [ ] 解决 basename collision。
* [ ] formal status 枚举化。
* [ ] `configs/skills.json` 移出 formal runtime。
* [ ] 新建 frozen base policy。
* [ ] 新建 PolicyState schema。
* [ ] registry 改成单 active policy。
* [ ] 每条 repair result 记录 policy hash。

## P1：最小闭环

* [ ] Blue Capability Packet。
* [ ] challenged-policy binding。
* [ ] 3-seed hardness probe。
* [ ] residual archive。
* [ ] grouped adaptation/target split。
* [ ] 四维局部 policy search。
* [ ] paired promotion replay。
* [ ] 完成 B0→B1。
* [ ] 完成 R1(B1)。
* [ ] 完成 B1→B2。

## P2：研究强化

* [ ] MAP-Elites archive。
* [ ] lineage deepening。
* [ ] learnability teacher。
* [ ] prompt lens search。
* [ ] model routing。
* [ ] population policy。
* [ ] 完整 RQ1–RQ5。

---

# 二十四、最终成功标准

## 工程成功

* 每次运行绑定 commit SHA、toolchain hash、policy hash 和 manifest hash；
* formal runtime 中只有一个 active policy；
* 手工 skill 无法冒充自动晋升；
* promotion 可重建、可拒绝、可回滚；
* round 可中断恢复；
* 公开仓库能复现小规模闭环。

## 实验成功

至少观察到：

[
B_0
\rightarrow
B_1
\rightarrow
B_2
]

并满足：

1. B1 恢复部分 B0 residual；
2. B1 non-target 无不可接受回归；
3. R1 明确攻击 B1；
4. R1 找到新 residual cell；
5. B2 恢复部分 B1 residual；
6. 多 seed 方向一致；
7. 增益不完全由预算增加解释。

## 论文叙事成功

最终主张应收敛为：

> R³E 不假设单条成功修复轨迹能够形成稳定可迁移的 runtime memory。红方针对当前 active repair policy 搜索合法、新颖且可学习的残余失败；这些失败驱动冻结策略空间中的整体 child-policy 搜索。只有在目标冻结重放上获得增益、且通过非目标回归检查的 child 才能由 formal registry 原子晋升。晋升后的 child 随后接受新一轮红方攻击，从而形成不更新模型权重的、可验证的红方引导策略演化。

---

# 二十五、推荐实施顺序

严格按照以下顺序推进：

```text
Oracle P0 修复
→ Formal Registry V2
→ Policy-driven repair runtime
→ Active-blue-conditioned red
→ 四维 child-policy search
→ B0→B1→B2 两轮闭环
→ 再增加 archive、model routing 和 population
```

在 B0→B1→B2 跑通前，不建议同时启动：

* Skill IR；
* 自动 AST 规则学习；
* 大规模多模型混战；
* 跨模块毒；
* 后端 WNS 闭环；
* 开放式自由文本记忆。

当前最关键的系统事实只有一个：

> 红方必须攻击当前 active policy，整体 child policy 必须经冻结验证晋升，晋升后必须再次被红方攻击。

只要这一闭环在多 seed、固定预算和唯一 formal registry 下成立，R³E 的动态进化主张就真正建立起来了。
