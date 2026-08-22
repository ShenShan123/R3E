# R³E-AIC：自进化 RTL 智能纠错系统

R³E-AIC 面向“RTL 能通过编译但功能仍错误”的芯片前端调试问题。比赛版把科研核心收敛成一条可审计链路：

```text
RTL → 缺陷诊断 → 多候选修复 → Parse → Compile → Simulation → Oracle → Formal → Regression
```

模型只提出候选，真实 EDA 工具链和 R³E `oracle_gate` 决定候选能否被接受。

## 30 秒了解作品

- Counter：发现 temporal/event-control 错误；
- FSM：发现可编译但状态输出错误；
- Shift：发现 blocking assignment 导致的 bit-level dataflow 错误；
- Experience / Evolution 页面只展示有冻结证据支持的回放，不把 deterministic fixture 当作模型效果。

## 一键运行

在仓库根目录执行：

```bash
./competition/scripts/setup.sh
./competition/scripts/run_demo.sh
./competition/scripts/reproduce_benchmarks.sh
```

`run_demo.sh` 默认使用冻结参考候选进行 guided replay，因此不需要模型 API；每个候选仍经过 Icarus、R³E differential oracle、Yosys 和重复仿真检查。该模式是可复现演示，不是模型增益实验。显式使用 `--mode live` 才会调用真实 provider，provider 不可用时直接报错，不伪造结果。

## 目录

- `app/`：轻量 HTTP API 和无构建依赖的演示界面；
- `services/`：diagnosis、repair、verification、evolution、memory、benchmark facade；
- `cases/`：三个可追溯到公开 CirFix 输入的演示案例；
- `configs/`：比赛模式、Demo 和 provider 配置模板；
- `results/frozen/`：只保存有来源的冻结证据索引；
- `scripts/`：安装检查、演示、结果冻结、匿名化与提交包检查；
- `docs/`：架构、视频脚本和官方大纲对齐的提交材料。

## 真实性边界

第三方 benchmark、Icarus Verilog、Yosys 和 provider 均在材料中注明来源。当前仓库不内置模型响应、私有日志或 headline 实验数字；`metrics.json` 的空值表示尚未冻结真实实验，而不是零结果。所有正式数字必须由 `freeze_results.py` 从 raw result artifact 生成。
