from __future__ import annotations

from pathlib import Path

from competition.services.benchmark_service import BenchmarkService
from competition.services.evolution_service import EvolutionService
from competition.services.memory_service import MemoryService


ROOT = Path(__file__).resolve().parents[2]


def test_unfrozen_authorities_remain_unavailable():
    assert BenchmarkService(ROOT).dashboard()["metrics"]["available"] is False
    assert EvolutionService(ROOT).timeline()["available"] is False
    assert MemoryService(ROOT).list_memories()["available"] is False
