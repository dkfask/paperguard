"""Sprint 2 性能基线测试 — 100 段论文 < 200ms.

验证 YAML pack engine (25 rules) 在 100 段 fixture 上的端到端性能。
基线: < 200ms（plan §7 DoD + Sprint 1.5 plan §3 性能要求继承）。

设计：跑多次取最短（去掉冷启动 / GC 抖动），避免单次测量噪声。
"""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.engine import validate_docx
from app.core.engine.loader import load_pack
from app.core.loaders.docx_loader import load_docx


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "sample_paper_100paras.docx"
PERF_BUDGET_MS = 200
PACK_ID = "GB-T-7713-1-2006"


class EnginePerfBaseline(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not FIXTURE_PATH.exists():
            raise FileNotFoundError(f"Fixture missing: {FIXTURE_PATH}")
        # 预加载 pack + IR（避免 import / IO 计入性能测量）
        cls.ir = load_docx(str(FIXTURE_PATH))
        cls.pack = load_pack(PACK_ID)
        # 暖一次（让 jmespath.compile 进 lru_cache）
        validate_docx(cls.ir, PACK_ID)

    def test_100_paragraphs_under_200ms(self):
        """100 段 × 25 rules 端到端跑通 < 200ms。"""
        timings = []
        for _ in range(5):
            start = time.perf_counter()
            findings = validate_docx(self.ir, PACK_ID)
            elapsed = time.perf_counter() - start
            timings.append(elapsed)
        best_ms = min(timings) * 1000
        self.assertLess(
            best_ms,
            PERF_BUDGET_MS,
            f"100 段论文 < {PERF_BUDGET_MS}ms 失败；"
            f"best={best_ms:.1f}ms timings={[f'{t*1000:.1f}ms' for t in timings]}",
        )
        # 结果至少非空（fixture 已知含违规段；空说明 YAML pack 或 fixture 失效）
        self.assertGreater(len(findings), 0,
                           "100-para fixture 应触发至少 1 个 finding")

    def test_repeat_invocations_stay_under_budget(self):
        """重复调用（模拟 LiveFormatChecker debounce 反复 check）依然 < 200ms。

        jmespath.compile 第二次起 lru_cache 命中；中位数应远低于上限。
        """
        timings = []
        for _ in range(10):
            start = time.perf_counter()
            validate_docx(self.ir, PACK_ID)
            timings.append((time.perf_counter() - start) * 1000)
        timings.sort()
        median_ms = timings[len(timings) // 2]
        self.assertLess(
            median_ms,
            PERF_BUDGET_MS,
            f"10 次调用中位数 {median_ms:.1f}ms 应 < {PERF_BUDGET_MS}ms；"
            f"all={[f'{t:.1f}' for t in timings]}",
        )


if __name__ == "__main__":
    unittest.main()
