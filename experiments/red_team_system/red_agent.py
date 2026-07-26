#!/usr/bin/env python3
"""
Red Agent - 红队核心决策模块
负责与 LLM API 交互，根据当前 RTL 上下文和历史经验生成 Multi-action 投毒 JSON
"""

import json
import math
import os
import shutil
import time
import uuid
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None
from pathlib import Path


RELEASE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROJECT_ROOT = Path(os.environ.get("R3E_RED_PROJECT_ROOT", RELEASE_ROOT / "third_party" / "rtl"))
DEFAULT_ARTIFACT_ROOT = Path(os.environ.get("R3E_ARTIFACT_ROOT", RELEASE_ROOT / "artifacts"))
DEFAULT_MEMORY_ROOT = Path(os.environ.get("R3E_RED_MEMORY_ROOT", DEFAULT_ARTIFACT_ROOT / "red_team/memory"))


@dataclass
class PoisonAction:
    """单个投毒动作的数据结构"""
    target_file: str  # 目标文件路径
    action_type: str  # 'modify_rtl' | 'modify_sdc' | 'inject_macro'
    level: int  # 1-2 投毒级别
    location: Dict[str, int]  # {'line_start': x, 'line_end': y} 或 {'function': 'xxx'}
    payload: str  # 具体的恶意代码片段
    rationale: str  # 投毒理由（用于记忆蒸馏）
    stealth_score: float  # 隐蔽性自评分 (0-1)


@dataclass
class PoisonPlan:
    """完整的投毒计划"""
    actions: List[PoisonAction]
    target_level: int  # 本次攻击的目标级别
    strategy: str  # 攻击策略描述
    expected_backend_impact: str  # 预期后端影响


class RedAgent:
    """红队 LLM 决策引擎"""
    ALLOWED_LEVELS = {1, 2}
    ALLOWED_ACTION_TYPES = {"modify_rtl", "modify_sdc", "inject_macro"}

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-flash",
        memory_dir: str = str(DEFAULT_MEMORY_ROOT),
        project_root: str = str(DEFAULT_PROJECT_ROOT),
        sandbox_work_root: Optional[str] = None,
        base_url: str = "https://api.deepseek.com"
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = OpenAI(api_key=api_key, base_url=base_url) if OpenAI else None
        self.model = model
        self.memory_dir = Path(memory_dir)
        self.project_root = Path(project_root)
        self.sandbox_work_root = Path(sandbox_work_root) if sandbox_work_root else None
        self.last_sandbox_root: Optional[Path] = None
        self.last_materialized_root: Optional[Path] = None
        self.last_plan_path: Optional[Path] = None

        # 投毒级别定义
        self.level_definitions = {
            1: "性能降级 - 前端通过，Yosys 综合后 Area/Power 暴增",
            2: "时序/物理违例 - 前端通过，后端 OpenROAD STA 结果恶化"
        }

    def _validate_level(self, target_level: int):
        if target_level not in self.ALLOWED_LEVELS:
            raise ValueError(f"Only Level 1/2 are allowed in red-sandbox-impl-r1, got Level {target_level}")

    def load_historical_memory(self) -> Dict[str, List[Dict]]:
        """加载历史投毒经验"""
        memory = {
            'success': [],
            'failure': [],
            'mutation_seeds': []
        }

        for category in memory.keys():
            category_dir = self.memory_dir / category
            if category_dir.exists():
                for mem_file in category_dir.glob("*.json"):
                    with open(mem_file, 'r', encoding='utf-8') as f:
                        memory[category].append(json.load(f))

        return memory

    def read_rtl_context(self, target_files: List[str]) -> Dict[str, str]:
        """读取目标 RTL 文件内容"""
        context = {}
        for file_path in target_files:
            full_path = self._resolve_project_file(file_path)
            if full_path.exists():
                with open(full_path, 'r', encoding='utf-8') as f:
                    context[file_path] = f.read()
        return context

    def build_system_prompt(self, target_level: int) -> str:
        """构建红队 LLM 的系统提示词"""
        self._validate_level(target_level)
        return f"""你是一位顶级的硬件安全红队专家，专注于 EDA 工具链的跨层隐蔽投毒攻击。

【核心约束 - 硬性底线】
1. 所有投毒必须 100% 通过前端语法检查（Yosys read_verilog/hierarchy/check）
2. 绝不允许简单的删符号、拼写错误等低级手段
3. 破坏必须在后端 OpenROAD STA 或综合评估阶段才爆发
4. 只允许本提示词列明的级别和动作类型

【本次任务目标级别】
Level {target_level}: {self.level_definitions[target_level]}

【投毒技术库】
Level 1 技术：
- 插入冗余逻辑门（如 wire x = a & a & a & a;）
- 展开状态机（将 case 语句改为嵌套 if-else）
- 滥用 generate 块生成大量重复电路

Level 2 技术：
- 篡改 SDC 约束文件（set_clock_uncertainty 设置极端值）
- 插入长组合逻辑链（多级门延迟累积）
- 修改时钟域交叉处理（移除同步器）

【输出格式要求】
必须返回严格的 JSON 格式，包含以下字段：
{{
  "actions": [
    {{
      "target_file": "相对于项目根目录的路径",
      "action_type": "modify_rtl | modify_sdc | inject_macro",
      "level": 1-2,
      "location": {{"line_start": x, "line_end": y}},
      "payload": "具体的恶意代码片段（必须是合法语法）",
      "rationale": "为什么这个投毒能通过前端但破坏后端",
      "stealth_score": 0.0-1.0
    }}
  ],
  "target_level": {target_level},
  "strategy": "本次攻击的整体策略描述",
  "expected_backend_impact": "预期的后端工具输出变化（如 WNS=-5.2ns, Area +300%）"
}}

【关键提醒】
- 可以并行输出多个 actions（Multi-action）
- 每个 payload 必须是完整的、可直接替换的代码片段
- rationale 字段将用于后续的记忆蒸馏和变异生成
"""

    def build_user_prompt(
        self,
        rtl_context: Dict[str, str],
        historical_memory: Dict[str, List[Dict]],
        target_level: int
    ) -> str:
        """构建用户提示词（包含上下文和历史经验）"""
        prompt_parts = [
            "【当前 RTL 上下文】",
            "以下是目标文件的当前内容：\n"
        ]

        for file_path, content in rtl_context.items():
            # 限制上下文长度，只取前100行
            lines = content.split('\n')[:100]
            prompt_parts.append(f"=== {file_path} ===")
            prompt_parts.append('\n'.join(lines))
            if len(content.split('\n')) > 100:
                prompt_parts.append("... (文件过长，已截断)")
            prompt_parts.append("")

        # 添加成功经验
        if historical_memory['success']:
            prompt_parts.append("\n【历史成功投毒案例（可参考但需变异）】")
            for i, mem in enumerate(historical_memory['success'][-5:], 1):  # 只取最近5条
                prompt_parts.append(f"案例 {i}:")
                prompt_parts.append(f"  Level: {mem.get('level', 'N/A')}")
                prompt_parts.append(f"  策略: {mem.get('strategy', 'N/A')}")
                prompt_parts.append(f"  奖励: {mem.get('reward', 'N/A')}")
                prompt_parts.append("")

        # 添加失败禁止项
        if historical_memory['failure']:
            prompt_parts.append("\n【历史失败案例（严禁重复）】")
            for i, mem in enumerate(historical_memory['failure'][-3:], 1):
                prompt_parts.append(f"失败 {i}:")
                prompt_parts.append(f"  原因: {mem.get('failure_reason', 'N/A')}")
                prompt_parts.append("")

        # 添加高优变异种子
        if historical_memory['mutation_seeds']:
            prompt_parts.append("\n【待变异高优种子（已被蓝方修复，需升级）】")
            for i, mem in enumerate(historical_memory['mutation_seeds'][-3:], 1):
                prompt_parts.append(f"种子 {i}:")
                prompt_parts.append(f"  原始策略: {mem.get('original_strategy', 'N/A')}")
                prompt_parts.append(f"  蓝方修复方式: {mem.get('blue_fix_method', 'N/A')}")
                prompt_parts.append("")

        prompt_parts.append(f"\n【任务】请针对 Level {target_level} 生成投毒计划，严格遵守 JSON 格式输出。")

        return '\n'.join(prompt_parts)

    def generate_poison_plan(
        self,
        target_files: List[str],
        target_level: int = 2,
        temperature: float = 0.8
    ) -> Optional[PoisonPlan]:
        """
        生成投毒计划

        Args:
            target_files: 目标文件列表
            target_level: 投毒级别 (1-2)
            temperature: LLM 采样温度

        Returns:
            PoisonPlan 对象，如果生成失败则返回 None
        """
        try:
            self._validate_level(target_level)
        except ValueError as e:
            print(f"[RedAgent] Scope 拒绝: {e}")
            return None

        # 加载历史记忆
        historical_memory = self.load_historical_memory()

        # 读取 RTL 上下文
        rtl_context = self.read_rtl_context(target_files)

        if not rtl_context:
            print(f"[RedAgent] 警告: 无法读取任何目标文件")
            return None

        # 构建提示词
        system_prompt = self.build_system_prompt(target_level)
        user_prompt = self.build_user_prompt(rtl_context, historical_memory, target_level)

        try:
            response_text = self._call_deepseek(system_prompt, user_prompt, temperature)

            # 尝试解析 JSON（可能包含在 markdown 代码块中）
            json_text = self._extract_json(response_text)
            plan_dict = json.loads(json_text)

            # 验证必需字段
            if 'actions' not in plan_dict or not plan_dict['actions']:
                print(f"[RedAgent] 错误: LLM 返回的计划缺少 actions 字段")
                return None

            # 构建 PoisonPlan 对象
            actions = [
                PoisonAction(
                    target_file=a['target_file'],
                    action_type=a['action_type'],
                    level=a['level'],
                    location=a['location'],
                    payload=a['payload'],
                    rationale=a['rationale'],
                    stealth_score=a.get('stealth_score', 0.5)
                )
                for a in plan_dict['actions']
            ]

            plan = PoisonPlan(
                actions=actions,
                target_level=plan_dict.get('target_level', target_level),
                strategy=plan_dict.get('strategy', ''),
                expected_backend_impact=plan_dict.get('expected_backend_impact', '')
            )

            try:
                self._validate_plan_scope(plan, expected_level=target_level)
                plan_targets = {action.target_file for action in plan.actions}
                allowed_targets = set(target_files)
                unknown_targets = sorted(plan_targets - allowed_targets)
                if unknown_targets:
                    raise ValueError(f"Plan targets not supplied for this run: {unknown_targets}")
            except ValueError as e:
                print(f"[RedAgent] Scope 拒绝: {e}")
                return None

            print(f"[RedAgent] 成功生成投毒计划: {len(actions)} 个动作, Level {target_level}")
            return plan

        except json.JSONDecodeError as e:
            print(f"[RedAgent] JSON 解析错误: {e}")
            print(f"原始响应: {response_text[:500]}")
            return None
        except Exception as e:
            print(f"[RedAgent] API 错误: {e}")
            return None

    def _extract_json(self, text: str) -> str:
        """从可能包含 markdown 的文本中提取 JSON"""
        # 尝试提取 ```json ... ``` 代码块
        if '```json' in text:
            start = text.find('```json') + 7
            end = text.find('```', start)
            return text[start:end].strip()
        elif '```' in text:
            start = text.find('```') + 3
            end = text.find('```', start)
            return text[start:end].strip()
        else:
            # 假设整个文本就是 JSON
            return text.strip()

    def _call_deepseek(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        if self.client is not None:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=4096
            )
            return response.choices[0].message.content or ""

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 4096
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek HTTP {e.code}: {body[:500]}") from e

        return data["choices"][0]["message"].get("content") or ""

    def _resolve_project_file(self, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise ValueError(f"Target path must be relative to project_root: {relative_path}")
        root = self.project_root.resolve()
        resolved = (root / candidate).resolve()
        if resolved == root or root not in resolved.parents:
            raise ValueError(f"Target path escapes project_root: {relative_path}")
        return resolved

    def _validate_plan_scope(self, plan: PoisonPlan, expected_level: Optional[int] = None):
        self._validate_level(plan.target_level)
        if expected_level is not None and plan.target_level != expected_level:
            raise ValueError(
                f"Plan target_level {plan.target_level} does not match requested Level {expected_level}"
            )
        for action in plan.actions:
            self._resolve_project_file(action.target_file)
            if action.level != plan.target_level:
                raise ValueError(
                    f"Action {action.target_file} Level {action.level} does not match plan Level {plan.target_level}"
                )
            if action.action_type not in self.ALLOWED_ACTION_TYPES:
                raise ValueError(f"Forbidden action_type: {action.action_type}")
            suffix = Path(action.target_file).suffix.lower()
            if action.action_type == "modify_sdc" and suffix != ".sdc":
                raise ValueError("modify_sdc may only target an .sdc file")
            if action.action_type in {"modify_rtl", "inject_macro"} and suffix not in {".v", ".sv"}:
                raise ValueError(f"{action.action_type} may only target a .v/.sv file")
            if not isinstance(action.payload, str) or not action.payload.strip():
                raise ValueError(f"Action {action.target_file} has an empty payload")
            if not math.isfinite(float(action.stealth_score)) or not 0.0 <= float(action.stealth_score) <= 1.0:
                raise ValueError(f"Action {action.target_file} has invalid stealth_score")
            if action.action_type == "modify_rtl":
                start = action.location.get("line_start")
                end = action.location.get("line_end")
                if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
                    raise ValueError(f"Action {action.target_file} has invalid line range")

    def _new_sandbox_root(self) -> Path:
        if self.sandbox_work_root:
            self.sandbox_work_root.mkdir(parents=True, exist_ok=True)
            return self.sandbox_work_root
        exec_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        root = DEFAULT_ARTIFACT_ROOT / "red_team" / f"red_sandbox_{exec_id}"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _assert_within_sandbox(self, path: Path, sandbox_root: Path):
        resolved = path.resolve()
        root = sandbox_root.resolve()
        if resolved != root and root not in resolved.parents:
            raise PermissionError(f"write outside red sandbox: {resolved}")

    def _materialize_targets(self, plan: PoisonPlan, sandbox_root: Path) -> Tuple[Path, Dict[str, Path]]:
        materialized_root = sandbox_root / "materialized"
        self._assert_within_sandbox(materialized_root, sandbox_root)
        materialized_root.mkdir(parents=True, exist_ok=True)

        copied: Dict[str, Path] = {}
        for action in plan.actions:
            source_path = self._resolve_project_file(action.target_file)
            if not source_path.is_file():
                print(f"[RedAgent] 警告: 目标文件不存在 {action.target_file}")
                continue

            target_path = materialized_root / action.target_file
            self._assert_within_sandbox(target_path, materialized_root)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if action.target_file not in copied:
                shutil.copy2(source_path, target_path)
                copied[action.target_file] = target_path

        return materialized_root, copied

    def _write_plan_artifact(self, plan: PoisonPlan, sandbox_root: Path) -> Path:
        artifact_dir = sandbox_root / "artifacts"
        self._assert_within_sandbox(artifact_dir, sandbox_root)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        plan_path = artifact_dir / "poison_plan.json"
        self._assert_within_sandbox(plan_path, sandbox_root)
        with open(plan_path, 'w', encoding='utf-8') as f:
            json.dump(asdict(plan), f, indent=2, ensure_ascii=False)
        return plan_path

    def execute_poison_plan(self, plan: PoisonPlan) -> Tuple[bool, List[str]]:
        """
        执行投毒计划（只修改红队隔离副本）

        Args:
            plan: 投毒计划对象

        Returns:
            (是否成功, 修改的文件列表)
        """
        try:
            self._validate_plan_scope(plan)
        except ValueError as e:
            print(f"[RedAgent] Scope 拒绝: {e}")
            return False, []

        modified_files = []
        sandbox_root = self._new_sandbox_root()
        self.last_sandbox_root = sandbox_root

        try:
            materialized_root, copied_targets = self._materialize_targets(plan, sandbox_root)
            self.last_materialized_root = materialized_root
            self.last_plan_path = self._write_plan_artifact(plan, sandbox_root)
        except Exception as e:
            print(f"[RedAgent] 沙箱准备失败: {e}")
            return False, modified_files

        for action in plan.actions:
            target_path = copied_targets.get(action.target_file)

            if not target_path or not target_path.exists():
                print(f"[RedAgent] 警告: 目标文件不存在 {action.target_file}")
                continue

            try:
                self._assert_within_sandbox(target_path, sandbox_root)

                # 读取原始文件
                with open(target_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()

                # 根据 action_type 执行不同的修改策略
                if action.action_type == 'modify_rtl':
                    # 替换指定行范围
                    start = action.location.get('line_start', 1) - 1
                    end = action.location.get('line_end', start + 1)
                    if start >= len(lines) or end > len(lines):
                        raise ValueError(
                            f"line range {start + 1}:{end} exceeds {len(lines)} lines"
                        )
                    lines[start:end] = [action.payload + '\n']

                elif action.action_type == 'inject_macro':
                    # 在文件开头注入宏定义
                    lines.insert(0, action.payload + '\n')

                elif action.action_type == 'modify_sdc':
                    # 修改 SDC 约束
                    lines.append('\n' + action.payload + '\n')

                else:
                    print(f"[RedAgent] 不支持的 action_type: {action.action_type}")
                    return False, modified_files

                # 写回文件
                self._assert_within_sandbox(target_path, sandbox_root)
                with open(target_path, 'w', encoding='utf-8') as f:
                    f.writelines(lines)

                modified_files.append(action.target_file)
                print(f"[RedAgent] 已投毒: {action.target_file} (Level {action.level})")

            except Exception as e:
                print(f"[RedAgent] 执行投毒失败 {action.target_file}: {e}")
                return False, modified_files

        if not modified_files:
            print("[RedAgent] 投毒执行失败: 没有任何文件被修改")
            return False, modified_files

        return True, modified_files
