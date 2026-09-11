"""StreamWorker 流水线单元测试（任务 2.3）。

覆盖：
- PostProcessor：ROI 过滤 / 最小尺寸过滤
- EventDetector：投票命中、断帧重置、冷却期
- StreamWorker 静态方法：TaskBinding 管理、max_fps 聚合

不依赖真实 RTSP / 模型文件（用模拟帧 + 模拟检测对象）。
"""
import time

import pytest

from src.plugins.builtin.ai_vision.runtime.worker import (
    EventDetector,
    PostProcessor,
    StreamWorker,
    TaskBinding,
)


# ── 测试替身 ─────────────────────────────────────────────

class FakeDetection:
    """模拟 engine.Detection（只含 PostProcessor 需要的字段）。"""

    def __init__(self, bbox, conf=0.9, cls_name="person"):
        self.bbox = bbox
        self.conf = conf
        self.cls_name = cls_name
        self.cls_id = 0


@pytest.fixture
def base_rule() -> dict:
    return {
        "threshold": 0.45,
        "duration": 0,
        "vote_seconds": 0,
        "cooldown": 60,
        "fps": 2,
        "roi": [],
        "schedule": [],
    }


# ── PostProcessor ─────────────────────────────────────────

class TestPostProcessor:
    def test_min_size_filter(self):
        pp = PostProcessor(roi=[], min_size=20, threshold=0.45)
        dets = [
            FakeDetection([0, 0, 10, 10]),   # 太小 → 过滤
            FakeDetection([0, 0, 100, 100]),  # 保留
        ]
        out = pp.filter(dets)
        assert len(out) == 1
        assert out[0].bbox == [0, 0, 100, 100]

    def test_roi_inside(self):
        # ROI 三角形 (0,0)-(100,0)-(0,100)，中心 (25,25) 在内，(200,200) 在外
        roi = [[0, 0], [100, 0], [0, 100]]
        pp = PostProcessor(roi=roi, min_size=5, threshold=0.45)
        dets = [
            FakeDetection([20, 20, 30, 30]),    # 中心 25,25 → 在内
            FakeDetection([180, 180, 220, 220]),  # 中心 200,200 → 在外
        ]
        out = pp.filter(dets)
        assert len(out) == 1
        assert out[0].bbox == [20, 20, 30, 30]

    def test_roi_empty_means_full_frame(self):
        pp = PostProcessor(roi=[], min_size=5, threshold=0.45)
        out = pp.filter([FakeDetection([0, 0, 30, 30]), FakeDetection([50, 50, 80, 80])])
        assert len(out) == 2


# ── EventDetector ─────────────────────────────────────────

class TestEventDetector:
    def test_single_frame_alarm(self, base_rule):
        """vote_seconds=0 → 单帧命中即告警。"""
        det = EventDetector(base_rule, fps=2)
        assert det.feed([FakeDetection([0, 0, 30, 30])], ts=100.0) is True

    def test_vote_window_requires_n_frames(self, base_rule):
        """vote_seconds=2, fps=2 → 需连续 4 帧命中才告警。"""
        rule = {**base_rule, "vote_seconds": 2}
        det = EventDetector(rule, fps=2)
        for i in range(3):
            assert det.feed([FakeDetection([0, 0, 30, 30])], ts=100.0 + i) is False
        # 第 4 帧命中 → 告警
        assert det.feed([FakeDetection([0, 0, 30, 30])], ts=103.0) is True

    def test_vote_window_reset_on_miss(self, base_rule):
        """断帧后窗口重置（一期简化：一旦未命中清零）。"""
        rule = {**base_rule, "vote_seconds": 2}
        det = EventDetector(rule, fps=2)
        det.feed([FakeDetection([0, 0, 30, 30])], ts=100.0)
        det.feed([FakeDetection([0, 0, 30, 30])], ts=101.0)
        det.feed([], ts=102.0)  # 断帧
        # 需要重新累积 4 帧
        for i in range(3):
            assert det.feed([FakeDetection([0, 0, 30, 30])], ts=103.0 + i) is False
        assert det.feed([FakeDetection([0, 0, 30, 30])], ts=106.0) is True

    def test_cooldown_suppresses_repeat(self, base_rule):
        """冷却期内不重复告警。"""
        rule = {**base_rule, "vote_seconds": 0, "cooldown": 60}
        det = EventDetector(rule, fps=2)
        assert det.feed([FakeDetection([0, 0, 30, 30])], ts=100.0) is True
        # 冷却期内（105 < 160）→ 不告警
        assert det.feed([FakeDetection([0, 0, 30, 30])], ts=105.0) is False
        # 冷却期外（161 > 160）→ 重新告警
        assert det.feed([FakeDetection([0, 0, 30, 30])], ts=161.0) is True


# ── StreamWorker（轻量，不启动线程）───────────────────────

class TestStreamWorker:
    def test_task_binding(self):
        w = StreamWorker(camera_id=1, rtsp_url="rtsp://x")
        task = TaskBinding(
            task_id=10, event_id=20, camera_id=1,
            category_codes=["person"], rule={}, model_ids=[1],
            analyze_fps=2,
        )
        w.add_task(task)
        assert w.task_count() == 1
        w.remove_task(10)
        assert w.task_count() == 0

    def test_max_fps_aggregation(self):
        w = StreamWorker(camera_id=1, rtsp_url="rtsp://x")
        assert w._max_fps() == 2  # 无任务默认 2
        w.add_task(TaskBinding(task_id=1, event_id=1, camera_id=1, category_codes=["person"], rule={}, model_ids=[1], analyze_fps=1))
        w.add_task(TaskBinding(task_id=2, event_id=2, camera_id=1, category_codes=["fire"], rule={}, model_ids=[2], analyze_fps=4))
        assert w._max_fps() == 4

    def test_snapshot_save_uses_last_frame(self, tmp_path):
        """抓拍图写入上传目录（模拟帧 640x480 黑图）。"""
        import numpy as np

        worker = StreamWorker(
            camera_id=7, rtsp_url="rtsp://x", uploads_dir=str(tmp_path),
        )
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        worker._last_frame = frame
        task = TaskBinding(task_id=1, event_id=1, camera_id=7, category_codes=["person"], rule={}, model_ids=[1])
        dets = [FakeDetection([100, 100, 300, 300], conf=0.95)]
        path = worker._save_snapshot(task, dets)
        assert path != ""
        assert path.endswith(".jpg")
        from pathlib import Path
        assert Path(path).exists()
        # 文件非空
        assert Path(path).stat().st_size > 0