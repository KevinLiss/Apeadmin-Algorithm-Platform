"""EventRule（判定规则）回归测试。

覆盖：face_selected_ids 字段解析、默认值、额外字段静默丢弃、
duration/vote_seconds 同步校验器。
"""
from src.plugins.builtin.ai_vision.schemas import EventRule


def test_default_face_selected_ids_is_empty_list():
    r = EventRule()
    assert r.face_selected_ids == []


def test_accepts_int_list():
    r = EventRule(face_mode="blacklist", face_selected_ids=[1, 2, 3])
    assert r.face_selected_ids == [1, 2, 3]


def test_extra_unknown_field_dropped():
    """worker 用到的字段必须在白名单声明，否则 PUT 时被静默丢弃（历史教训）。"""
    r = EventRule(unknown_field=123, another="x")
    assert "unknown_field" not in r.model_dump()


def test_duration_vote_sync():
    r = EventRule(duration=10, vote_seconds=0)
    assert r.vote_seconds == 10
    r2 = EventRule(duration=0, vote_seconds=7)
    assert r2.duration == 7


def test_face_selected_ids_survives_model_dump():
    r = EventRule(face_mode="stranger", face_selected_ids=[5])
    assert r.model_dump()["face_selected_ids"] == [5]
