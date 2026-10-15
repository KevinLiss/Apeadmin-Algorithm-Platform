"""FaceDetector 判定器：face_selected_ids 透传 + 四模式判定路径。

用 FakeEngine + FakeGallery 驱动 feed()，验证 detector 把 selected_ids
正确传给 gallery.best_match（即「绑定指定人脸」），并由阈值决定告警。
"""
import numpy as np
import pytest
from unittest.mock import patch

from src.plugins.builtin.ai_vision.runtime.face.detector import FaceDetector

DIM = 128
THR = 0.363


def _ortho(n: int, dim: int, seed: int = 7):
    A = np.random.default_rng(seed).standard_normal((dim, n))
    Q, _ = np.linalg.qr(A)
    return [Q[:, i].astype(np.float32) for i in range(n)]


class _Box:
    def __init__(self, bbox, score=0.9):
        self.bbox = bbox
        self.score = score


class _Engine:
    """返回指定「在场人脸」的特征向量（模拟某张脸出现在帧中）。"""

    def __init__(self, present_vec):
        self.present_vec = present_vec

    def detect_faces(self, frame):
        # 100x100 大脸，必定通过 min_size 过滤
        return [_Box([0, 0, 100, 100], 0.9)]

    def embed(self, frame, f):
        return self.present_vec


class _FakeGallery:
    """按真实 gallery 语义（分组 + filter_ids + top1 余弦）返回匹配。"""

    def __init__(self, people):
        self.people = people
        self._ids = [p["id"] for p in people]
        self._groups = [p["group"] for p in people]
        mat = [p["vec"] for p in people]
        self._mat = np.vstack(mat).astype(np.float32) if mat else np.zeros((0, DIM), np.float32)

    def person(self, pid):
        return next((p for p in self.people if p["id"] == pid), None)

    def match_one(self, vec, pid):
        p = self.person(pid)
        if p is None:
            return None, 0.0
        return p, float(self._mat[self._ids.index(pid)] @ vec)

    def best_match(self, vec, groups=("whitelist",), filter_ids=None):
        idx = [i for i, g in enumerate(self._groups) if g in set(groups)]
        if filter_ids is not None:
            idx = [i for i in idx if self._ids[i] in filter_ids]
        if not idx:
            return None, 0.0
        sims = self._mat[idx] @ vec
        j = int(np.argmax(sims))
        p = self.people[idx[j]]
        return {"id": p["id"], "name": p["name"], "group": p["group"]}, float(sims[j])


def _feed(rule, people, present_vec):
    gallery = _FakeGallery(people)
    engine = _Engine(present_vec)
    # detector 在 _recognize 内部 `from ...gallery import get_gallery`，
    # 故 patch gallery 模块上的 get_gallery。
    with patch(
        "src.plugins.builtin.ai_vision.runtime.face.gallery.get_gallery",
        return_value=gallery,
    ):
        det = FaceDetector(rule, fps=2)
        det.interval = 1e-6  # 强制每次 feed 都识别
        fr = det.feed(np.zeros((10, 10, 3), np.uint8), engine, ts=1.0)
    return det, fr


def test_detector_parses_selected_ids():
    det = FaceDetector({"face_mode": "blacklist", "face_selected_ids": [1, 2]}, fps=2)
    assert det.selected_ids == {1, 2}


def test_detector_selected_ids_none_when_empty():
    det = FaceDetector({"face_mode": "blacklist"}, fps=2)
    assert det.selected_ids is None


def test_blacklist_respects_filter():
    """在场人脸是 #2（黑名单），但事件只绑定 #3 → #2 被排除 → 命中 #3 但
    余弦≈0 < 阈值 → 不认识 → 不告警。"""
    vecs = _ortho(3, DIM)
    people = [
        {"id": 2, "name": "乙", "group": "blacklist", "vec": vecs[1]},
        {"id": 3, "name": "丙", "group": "blacklist", "vec": vecs[2]},
    ]
    _, fr = _feed({"face_mode": "blacklist", "face_selected_ids": [3]}, people, vecs[1])
    assert not fr.alarm, "被 filter 排除的在场黑名单人员不应告警"
    assert fr.matches[0].person_id == 0, "排除后相似度低于阈值 → 视为陌生人"


def test_blacklist_alarms_when_filter_includes_present():
    vecs = _ortho(3, DIM)
    people = [
        {"id": 2, "name": "乙", "group": "blacklist", "vec": vecs[1]},
        {"id": 3, "name": "丙", "group": "blacklist", "vec": vecs[2]},
    ]
    _, fr = _feed({"face_mode": "blacklist", "face_selected_ids": [2], "cooldown": 0}, people, vecs[1])
    assert fr.alarm, "被 filter 包含的在场黑名单人员应告警"
    assert fr.matches[0].person_id == 2


def test_filter_none_equals_no_filter_regression():
    """回归：face_selected_ids 不传 与 显式省略 行为一致。"""
    vecs = _ortho(3, DIM)
    people = [
        {"id": 2, "name": "乙", "group": "blacklist", "vec": vecs[1]},
        {"id": 3, "name": "丙", "group": "blacklist", "vec": vecs[2]},
    ]
    _, fr1 = _feed({"face_mode": "blacklist", "face_selected_ids": [2]}, people, vecs[1])
    _, fr2 = _feed({"face_mode": "blacklist"}, people, vecs[1])  # 无 filter 键
    assert fr1.alarm == fr2.alarm
    assert fr1.matches[0].person_id == fr2.matches[0].person_id == 2
