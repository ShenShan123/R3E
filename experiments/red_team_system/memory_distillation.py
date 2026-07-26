#!/usr/bin/env python3
"""
Memory Distillation - 双边记忆蒸馏与管理
管理红蓝对抗系统的经验库，实现记忆流转逻辑
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime
import hashlib
import tempfile
from contextlib import contextmanager
import fcntl


RELEASE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MEMORY_ROOT = Path(os.environ.get(
    "R3E_RED_MEMORY_ROOT",
    RELEASE_ROOT / "artifacts" / "red_team" / "memory",
))


@dataclass
class MemoryEntry:
    """单条记忆的数据结构"""
    memory_id: str  # 唯一标识符
    timestamp: float  # 创建时间戳
    poison_level: int  # 投毒级别 (1-4)
    target_files: List[str]  # 目标文件列表
    actions: List[Dict[str, Any]]  # 投毒动作列表
    strategy: str  # 攻击策略描述

    # 前端检查结果
    frontend_passed: bool
    frontend_errors: List[str]

    # 后端结果
    synthesis_passed: Optional[bool]
    timing_passed: Optional[bool]
    area: Optional[float]
    gate_count: Optional[int]
    wns: Optional[float]
    tns: Optional[float]

    # 蓝方响应
    repair_iterations: int  # 蓝方修复轮数
    repair_success: bool  # 是否被成功修复
    blue_fix_method: Optional[str]  # 蓝方修复方法描述

    # 奖励评分
    reward: float
    reward_breakdown: Dict[str, float]

    # 元数据
    category: str  # 'pending' | 'success' | 'failure' | 'mutation_seeds'
    tags: List[str]  # 标签（如 'timing_attack', 'area_bloat', 'trojan'）
    notes: str  # 备注


class MemoryDistillation:
    """记忆蒸馏与管理系统"""

    def __init__(self, memory_dir: str = str(DEFAULT_MEMORY_ROOT)):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # 三个核心目录
        self.success_dir = self.memory_dir / "success"
        self.failure_dir = self.memory_dir / "failure"
        self.mutation_dir = self.memory_dir / "mutation_seeds"
        self.pending_dir = self.memory_dir / "pending"

        for d in [self.success_dir, self.failure_dir, self.mutation_dir, self.pending_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # 索引文件
        self.index_file = self.memory_dir / "memory_index.json"
        self.lock_file = self.memory_dir / ".memory_index.lock"
        self.index = self._load_index()

    @staticmethod
    def _empty_index() -> Dict[str, Dict]:
        return {'pending': {}, 'success': {}, 'failure': {}, 'mutation_seeds': {}}

    def _load_index(self) -> Dict[str, Dict]:
        """加载记忆索引"""
        if self.index_file.exists():
            with open(self.index_file, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            index = self._empty_index()
            for category in index:
                value = loaded.get(category, {})
                if not isinstance(value, dict):
                    raise ValueError(f"memory index category must be an object: {category}")
                index[category] = value
            return index
        return self._empty_index()

    @contextmanager
    def _locked(self):
        with self.lock_file.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _atomic_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def _save_index(self):
        """保存记忆索引"""
        self._atomic_json(self.index_file, self.index)

    def _generate_memory_id(self, actions: List[Dict], timestamp: float) -> str:
        """生成唯一的记忆 ID"""
        content = json.dumps(actions, sort_keys=True) + str(timestamp)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def update_memory(
        self,
        actions: List[Dict[str, Any]],
        strategy: str,
        poison_level: int,
        target_files: List[str],
        is_frontend_pass: bool,
        reward: float,
        reward_breakdown: Dict[str, float],
        is_fixed_by_blue: bool = False,
        frontend_errors: Optional[List[str]] = None,
        synthesis_passed: Optional[bool] = None,
        timing_passed: Optional[bool] = None,
        area: Optional[float] = None,
        gate_count: Optional[int] = None,
        wns: Optional[float] = None,
        tns: Optional[float] = None,
        repair_iterations: int = 0,
        blue_fix_method: Optional[str] = None,
        tags: Optional[List[str]] = None,
        notes: str = "",
        backend_evaluated: bool = True,
    ) -> str:
        """
        更新记忆库

        Args:
            actions: 投毒动作列表
            strategy: 攻击策略
            poison_level: 投毒级别
            target_files: 目标文件
            is_frontend_pass: 是否通过前端检查
            reward: 奖励分数
            reward_breakdown: 奖励详细分解
            is_fixed_by_blue: 是否被蓝方修复
            frontend_errors: 前端错误列表
            synthesis_passed: 综合是否通过
            timing_passed: 时序是否通过
            area: 面积
            gate_count: 门数量
            wns: WNS
            tns: TNS
            repair_iterations: 修复轮数
            blue_fix_method: 蓝方修复方法
            tags: 标签
            notes: 备注
            backend_evaluated: 是否已有可重建的后端验证结果

        Returns:
            记忆 ID

        记忆流转逻辑:
        1. 前端通过但后端尚未回传 -> pending 目录（不可作为成功经验）
        2. 前端检查失败 -> failure 目录（禁止项）
        3. 前端通过 + 后端破坏成功 + 未被修复 -> success 目录
        4. 前端通过 + 后端破坏成功 + 被蓝方修复 -> mutation_seeds 目录（高优种子）
        5. 前端通过 + 后端无破坏 -> failure 目录（无效攻击）
        """
        timestamp = time.time()
        memory_id = self._generate_memory_id(actions, timestamp)

        # 确定记忆类别
        if is_frontend_pass and not backend_evaluated:
            category = 'pending'
            notes = notes or "前端检查通过，等待蓝方后端验证；不可进入活跃成功记忆"
        elif not is_frontend_pass:
            category = 'failure'
            notes = notes or "前端检查失败，违反隐蔽性约束"
        elif reward > 0 and not is_fixed_by_blue:
            category = 'success'
            notes = notes or f"成功投毒，Level {poison_level}，奖励 {reward:.2f}"
        elif reward > 0 and is_fixed_by_blue:
            category = 'mutation_seeds'
            notes = notes or f"已被蓝方修复，作为变异种子保留（原奖励 {reward:.2f}）"
        else:
            category = 'failure'
            notes = notes or "后端无破坏效果，攻击无效"

        # 构建记忆条目
        memory = MemoryEntry(
            memory_id=memory_id,
            timestamp=timestamp,
            poison_level=poison_level,
            target_files=target_files,
            actions=actions,
            strategy=strategy,
            frontend_passed=is_frontend_pass,
            frontend_errors=frontend_errors or [],
            synthesis_passed=synthesis_passed,
            timing_passed=timing_passed,
            area=area,
            gate_count=gate_count,
            wns=wns,
            tns=tns,
            repair_iterations=repair_iterations,
            repair_success=is_fixed_by_blue,
            blue_fix_method=blue_fix_method,
            reward=reward,
            reward_breakdown=reward_breakdown,
            category=category,
            tags=tags or [],
            notes=notes
        )

        with self._locked():
            self.index = self._load_index()
            self._save_memory(memory)
            self._update_index(memory)

        print(f"[MemoryDistillation] 记忆已保存: {memory_id} -> {category}")
        return memory_id

    def _save_memory(self, memory: MemoryEntry):
        """保存记忆到文件"""
        if memory.category == 'success':
            target_dir = self.success_dir
        elif memory.category == 'failure':
            target_dir = self.failure_dir
        elif memory.category == 'pending':
            target_dir = self.pending_dir
        else:
            target_dir = self.mutation_dir

        file_path = target_dir / f"{memory.memory_id}.json"

        self._atomic_json(file_path, asdict(memory))

    def _update_index(self, memory: MemoryEntry):
        """更新索引"""
        category = memory.category

        self.index[category][memory.memory_id] = {
            'timestamp': memory.timestamp,
            'level': memory.poison_level,
            'reward': memory.reward,
            'strategy': memory.strategy[:100],  # 截断
            'tags': memory.tags,
            'file': f"{memory.memory_id}.json"
        }

        self._save_index()

    def load_memory(self, memory_id: str) -> Optional[MemoryEntry]:
        """加载指定记忆"""
        # 在所有状态目录中搜索
        for category_dir in [self.pending_dir, self.success_dir, self.failure_dir, self.mutation_dir]:
            file_path = category_dir / f"{memory_id}.json"
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return MemoryEntry(**data)

        print(f"[MemoryDistillation] 警告: 记忆 {memory_id} 不存在")
        return None

    def query_memories(
        self,
        category: Optional[str] = None,
        min_reward: Optional[float] = None,
        poison_level: Optional[int] = None,
        tags: Optional[List[str]] = None,
        limit: int = 10,
        sort_by: str = 'reward'  # 'reward' | 'timestamp'
    ) -> List[MemoryEntry]:
        """
        查询记忆

        Args:
            category: 记忆类别过滤
            min_reward: 最小奖励过滤
            poison_level: 级别过滤
            tags: 标签过滤
            limit: 返回数量限制
            sort_by: 排序方式

        Returns:
            记忆列表
        """
        results = []

        # 确定搜索范围
        if category:
            if category not in self.index:
                raise ValueError(f"unknown memory category: {category}")
            categories = [category]
        else:
            categories = ['pending', 'success', 'failure', 'mutation_seeds']

        # 遍历索引
        for cat in categories:
            for mem_id, meta in self.index[cat].items():
                # 应用过滤条件
                if min_reward is not None and meta['reward'] < min_reward:
                    continue
                if poison_level is not None and meta['level'] != poison_level:
                    continue
                if tags and not any(t in meta['tags'] for t in tags):
                    continue

                # 加载完整记忆
                memory = self.load_memory(mem_id)
                if memory:
                    results.append(memory)

        # 排序
        if sort_by == 'reward':
            results.sort(key=lambda m: m.reward, reverse=True)
        elif sort_by == 'timestamp':
            results.sort(key=lambda m: m.timestamp, reverse=True)

        return results[:limit]

    def get_top_successes(self, limit: int = 5) -> List[MemoryEntry]:
        """获取最成功的投毒案例"""
        return self.query_memories(
            category='success',
            limit=limit,
            sort_by='reward'
        )

    def get_mutation_seeds(self, limit: int = 5) -> List[MemoryEntry]:
        """获取待变异的高优种子"""
        return self.query_memories(
            category='mutation_seeds',
            limit=limit,
            sort_by='reward'
        )

    def get_forbidden_patterns(self) -> List[MemoryEntry]:
        """获取失败禁止项"""
        return self.query_memories(
            category='failure',
            limit=20,
            sort_by='timestamp'
        )

    def move_to_mutation_seeds(self, memory_id: str, blue_fix_method: str):
        """
        将成功记忆移动到变异种子库（当被蓝方修复时）

        Args:
            memory_id: 记忆 ID
            blue_fix_method: 蓝方修复方法描述
        """
        memory = self.load_memory(memory_id)
        if not memory:
            return

        if memory.category != 'success':
            raise ValueError(f"only success memory can become a mutation seed: {memory_id}")
        with self._locked():
            self.index = self._load_index()
            memory.category = 'mutation_seeds'
            memory.repair_success = True
            memory.blue_fix_method = blue_fix_method
            memory.notes += f"\n[{datetime.now().isoformat()}] 被蓝方修复，移入变异种子库"
            old_file = self.success_dir / f"{memory_id}.json"
            if old_file.exists():
                old_file.unlink()
            self._save_memory(memory)
            if memory_id in self.index['success']:
                del self.index['success'][memory_id]
            self._update_index(memory)

        print(f"[MemoryDistillation] 记忆 {memory_id} 已移动到变异种子库")

    def delete_memory(self, memory_id: str):
        """删除记忆"""
        with self._locked():
            self.index = self._load_index()
            for category_dir in [self.pending_dir, self.success_dir, self.failure_dir, self.mutation_dir]:
                file_path = category_dir / f"{memory_id}.json"
                if file_path.exists():
                    file_path.unlink()
                    print(f"[MemoryDistillation] 记忆 {memory_id} 已删除")
            for category in self.index.keys():
                if memory_id in self.index[category]:
                    del self.index[category][memory_id]
            self._save_index()

    def get_statistics(self) -> Dict[str, Any]:
        """获取记忆库统计信息"""
        stats = {
            'total': 0,
            'by_category': {},
            'by_level': {1: 0, 2: 0, 3: 0, 4: 0},
            'avg_reward': {},
            'top_reward': {}
        }

        for category in ['pending', 'success', 'failure', 'mutation_seeds']:
            count = len(self.index[category])
            stats['by_category'][category] = count
            stats['total'] += count

            # 计算平均奖励
            rewards = [meta['reward'] for meta in self.index[category].values()]
            if rewards:
                stats['avg_reward'][category] = sum(rewards) / len(rewards)
                stats['top_reward'][category] = max(rewards)
            else:
                stats['avg_reward'][category] = 0.0
                stats['top_reward'][category] = 0.0

            # 统计级别分布
            for meta in self.index[category].values():
                level = meta['level']
                if level in stats['by_level']:
                    stats['by_level'][level] += 1

        return stats

    def export_memories(self, output_file: str, category: Optional[str] = None):
        """导出记忆到单个 JSON 文件"""
        memories = self.query_memories(category=category, limit=1000)

        export_data = {
            'export_time': datetime.now().isoformat(),
            'total_count': len(memories),
            'memories': [asdict(m) for m in memories]
        }

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)

        print(f"[MemoryDistillation] 已导出 {len(memories)} 条记忆到 {output_file}")

    def prune_old_failures(self, keep_recent: int = 50):
        """清理旧的失败记忆（保留最近的 N 条）"""
        failures = self.query_memories(
            category='failure',
            limit=1000,
            sort_by='timestamp'
        )

        if len(failures) > keep_recent:
            to_delete = failures[keep_recent:]
            for memory in to_delete:
                self.delete_memory(memory.memory_id)

            print(f"[MemoryDistillation] 已清理 {len(to_delete)} 条旧失败记忆")


