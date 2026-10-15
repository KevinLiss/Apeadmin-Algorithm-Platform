"""FaceGallery.best_match(filter_ids) 回归测试。

filter_ids 是「白名单」语义：不在集合内的人员一定不出现在候选里；
是否在阈值之上由调用方（FaceDetector）判定。本测试只验证候选筛选。
"""
import threading
import time

import numpy as np

from src.plugins.builtin.ai_vision.runtime.face.gallery import FaceGallery

DIM = 128
THR = 0.363  # detector 默认余弦阈值


def _ortho(n: int, dim: int, seed: int = 42):
    """生成 n 个两两正交归一化向量（无噪声，便于精确判定命中谁）。"""
    A = np.random.default_rng(seed).standard_normal((dim, n))
    Q, _ = np.linalg.qr(A)
    return [Q[:, i].astype(np.float32) for i in range(n)]


def _make_gallery():
    """构造隔离实例（绕过单例与 _ensure 查库）。"""
    g = FaceGallery.__new__(FaceGallery)
    g._mu = threading.Lock()
    g._ids = [1, 2, 3]
    g._names = ["甲", "乙", "丙"]
    g._groups = ["whitelist", "whitelist", "blacklist"]
    vecs = _ortho(3, DIM)
    g._mat = np.vstack(vecs).astype(np.float32)
    g._loaded_at = time.time() + 1e9  # 让 _ensure 立即返回，不重载真实库
    return g, vecs


def test_no_filter_hits_best_match():
    g, v = _make_gallery()
    p, sim = g.best_match(v[1], groups=("whitelist", "blacklist"), filter_ids=None)
    assert p["id"] == 2 and sim > 0.99


def test_filter_excludes_unselected_ids():
    g, v = _make_gallery()
    p, _ = g.best_match(v[1], groups=("whitelist", "blacklist"), filter_ids={1, 3})
    assert p is not None and p["id"] != 2 and p["id"] in {1, 3}


def test_filter_includes_selected_id():
    g, v = _make_gallery()
    p, sim = g.best_match(v[1], groups=("whitelist", "blacklist"), filter_ids={2})
    assert p["id"] == 2 and sim > 0.99


def test_empty_filter_yields_no_match():
    g, v = _make_gallery()
    p, _ = g.best_match(v[1], groups=("whitelist", "blacklist"), filter_ids=set())
    assert p is None


def test_filter_outside_group_no_match():
    g, v = _make_gallery()
    p, _ = g.best_match(v[2], groups=("whitelist",), filter_ids={3})
    assert p is None


def test_filter_inside_group_hits():
    g, v = _make_gallery()
    p, sim = g.best_match(v[2], groups=("blacklist",), filter_ids={3})
    assert p["id"] == 3 and sim > 0.99


def test_filter_none_matches_default_regression():
    """回归：显式传 filter_ids=None 必须与原默认（不传）行为完全一致。"""
    g, v = _make_gallery()
    a = g.best_match(v[2], groups=("blacklist",))
    b = g.best_match(v[2], groups=("blacklist",), filter_ids=None)
    assert a[0] == b[0] and abs(a[1] - b[1]) < 1e-6
