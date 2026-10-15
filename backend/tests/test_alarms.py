"""告警中心 + 实时监控台：alarms-since 水位 + ack/false-positive/batch-ack 状态机。

依赖 tests/conftest.py 提供的 ai_client（聚焦路由 + 内存库 + 鉴权绕过）。
"""
from sqlalchemy import func

from src.plugins.builtin.ai_vision.models import AIVisionAlarm


async def _seed_alarms(factory, n=4, camera_id=1, event_id=1, status="pending"):
    async with factory() as db:
        objs = [
            AIVisionAlarm(
                task_id=1, event_id=event_id, camera_id=camera_id,
                category_code="person", confidence=0.9, status=status,
                level="warning",
            )
            for _ in range(n)
        ]
        db.add_all(objs)
        await db.commit()


async def test_alarms_since_returns_all_when_since_zero(ai_client, ai_factory):
    await _seed_alarms(ai_factory, n=4)
    r = await ai_client.get(
        "/api/v1/ai-vision/monitor/alarms-since", params={"since_id": 0}
    )
    assert r.status_code == 200
    data = r.json()["data"]
    ids = [a["id"] for a in data["items"]]
    assert ids == sorted(ids)  # 升序
    assert len(ids) == 4
    assert data["next_since_id"] == max(ids)


async def test_alarms_since_filters_by_id(ai_client, ai_factory):
    await _seed_alarms(ai_factory, n=4)
    r = await ai_client.get(
        "/api/v1/ai-vision/monitor/alarms-since", params={"since_id": 2}
    )
    data = r.json()["data"]
    items = data["items"]
    assert items == sorted(items, key=lambda x: x["id"])
    assert all(a["id"] > 2 for a in items)
    assert data["next_since_id"] == max(a["id"] for a in items)


async def test_alarms_since_camera_filter(ai_client, ai_factory):
    await _seed_alarms(ai_factory, n=2, camera_id=1)
    await _seed_alarms(ai_factory, n=2, camera_id=2)
    r = await ai_client.get(
        "/api/v1/ai-vision/monitor/alarms-since",
        params={"since_id": 0, "camera_id": 2},
    )
    items = r.json()["data"]["items"]
    assert len(items) == 2
    assert all(a["camera_id"] == 2 for a in items)


async def test_alarms_since_future_ts_returns_empty(ai_client, ai_factory):
    await _seed_alarms(ai_factory, n=3)
    r = await ai_client.get(
        "/api/v1/ai-vision/monitor/alarms-since",
        params={"since_ts": "2999-01-01T00:00:00"},
    )
    data = r.json()["data"]
    assert data["items"] == []
    assert data["next_since_ts"] == "2999-01-01T00:00:00"


async def test_ack_state_machine(ai_client, ai_factory):
    await _seed_alarms(ai_factory, n=1)
    r = await ai_client.post("/api/v1/ai-vision/alarms/1/ack")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["status"] == "acknowledged"
    assert d["ack_by"] == 1 and d["ack_at"] is not None


async def test_false_positive_state_machine(ai_client, ai_factory):
    await _seed_alarms(ai_factory, n=1)
    r = await ai_client.post(
        "/api/v1/ai-vision/alarms/1/false-positive", params={"note": "误报测试"}
    )
    d = r.json()["data"]
    assert d["status"] == "false_positive"
    assert d["note"] == "误报测试"


async def test_ack_unknown_id_404(ai_client, ai_factory):
    r = await ai_client.post("/api/v1/ai-vision/alarms/999/ack")
    assert r.status_code == 404


async def test_batch_ack(ai_client, ai_factory):
    await _seed_alarms(ai_factory, n=3)
    r = await ai_client.post(
        "/api/v1/ai-vision/alarms/batch-ack", params={"alarm_ids": [1, 2, 3]}
    )
    assert r.status_code == 200
    rr = await ai_client.get(
        "/api/v1/ai-vision/alarms", params={"status": "acknowledged"}
    )
    items = rr.json()["data"]["items"]
    assert len(items) == 3
