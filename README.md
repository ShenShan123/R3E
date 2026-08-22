# R³E：自进化RTL智能纠错系统

R³E 面向芯片前端设计中“RTL 能通过编译但功能仍错误”的问题，连接运行时证据、Failure Descriptor、Blue Candidate Portfolio、Scope Gate、真实 EDA 正确性验证和可追溯证据。R³E-AIC 仅是比赛软件版本名。

> 模型提出候选，EDA 决定候选。

## 一键演示

Python 3.11+、Icarus Verilog/VVP 和 Yosys 是比赛版基线依赖：

```bash
./competition/scripts/setup.sh
./competition/scripts/run_demo.sh
./competition/scripts/verify_benchmark_inputs.sh
```

`run_demo.sh` 默认是 guided replay：它从 buggy RTL 生成 checked-in 的最小修复候选，不调用模型，也不宣称模型效果；每个候选仍经过 Parse、Scope、Compile、Simulation、R³E differential Oracle、Yosys Structural Check 和 Repeatability Check。显式运行 `./competition/scripts/run_demo.sh --mode live` 才会调用真实 provider，provider 不可用时 fail closed。

## 比赛 Demo

1. **Counter Temporal Defect**：`always@(clk)` 触发时序错误；
2. **FSM Control Defect**：GNT3 状态缺少输出逻辑，代码可编译但功能错误；
3. **Shift Register Dataflow Defect**：blocking assignment 破坏移位寄存器的旧值传播。

启动本地界面：

```bash
PYTHONPATH=. python3 -m competition.app.backend.api
```

浏览器访问 `http://127.0.0.1:8080`。界面提供 Repair Studio、Verification Trace、Evolution Lab、Experience Memory 和 Benchmark Dashboard；没有冻结证据的演化、记忆和指标会明确显示不可用，不会填充伪结果。

## 目录

- `competition/`：AIC 比赛 Facade、案例、API/UI、配置、提交材料和一键脚本；
- `r3e/blue/portfolio/`：candidate portfolio、lens routing、候选执行和 runner-owned verification；
- `r3e/policy/`：active policy、child search、promotion 和 rollback；
- `r3e/arena/`：round orchestration、paired replay、manifest 和 audit；
- `r3e/memory/`：Verified Experience Memory / RAAM 协议和 activation guard；
- `r3e/red/`：Adversarial Bug Discovery 与 residual archive；
- `datasets/`：带 manifest/hash 的公开 benchmark 输入；
- `scripts/`：数据集核验和发布安全检查；
- `tests/`：R³E Core 离线协议、完整性和接口测试。

## 真实性和权限边界

比赛 Facade 不复制 R³E Core。Live provider 只负责 replacement RTL 提案，不能报告 gate 成功、排序候选或宣布修复正确；Diagnosis、Router、Scope、EDA 和 Candidate Receipt 均由 runner 负责。`r3e.semantic_repair_bench.oracle_gate.judge` 使用同一 testbench 对 oracle-only reference 和 candidate 做 Icarus differential comparison，结果由 runner 记录。

第三方 CirFix/Strider/RTLFixer benchmark、Icarus Verilog、Yosys、provider 和公开 RTL 的来源/许可见 `datasets/README.md`、`datasets/licenses/` 和 `competition/docs/submission/佐证材料索引.md`。Guided minimal patch 只能证明工程协议能力，不能作为 headline gain。

## 证据与复现

正式数字必须由 `competition/scripts/freeze_results.py` 从显式授权的 live raw summary 生成，并绑定 commit、数据 manifest、provider/model、seed、candidate budget、verifier、Icarus/Yosys 版本和结果 hash。当前 `competition/results/frozen/metrics.json` 的 pending/null 状态表示尚未冻结真实 benchmark 结果。

运行日志、模型响应、凭据和机器相关路径应放在仓库外；提交前运行：

```bash
python3 competition/scripts/check_anonymization.py
python3 scripts/check_release.py
python3 competition/scripts/build_submission_package.py
```

## Core 开发

完整 R³E Core 的离线协议测试可用仓库已有的 `pytest` 配置运行。真实 provider 测试使用 `network` 标记，必须单独授权和固定 call/token budget；本仓库默认不发起模型/API 调用。
