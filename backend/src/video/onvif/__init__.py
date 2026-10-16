"""ONVIF 协议接入模块。

ONVIF（Open Network Video Interface Forum）网络视频接口标准：
- 设备发现（WS-Discovery）
- 设备能力查询（GetCapabilities）
- 媒体服务（GetStreamUri / GetSnapshotUri）
- PTZ 云台控制（ContinuousMove / Stop）
- 事件订阅

使用示例：
```python
from src.video.onvif import ONVIFClient

client = ONVIFClient(
    host="192.168.1.100",
    port=80,
    username="admin",
    password="admin",
)
await client.connect()

# 获取流地址
uri = await client.get_stream_uri()
print(uri)  # rtsp://192.168.1.100:554/stream1

# PTZ 控制
await client.continuous_move(pan=0.5, tilt=0.0, zoom=0.0)
await client.stop()
```
"""

from .client import ONVIFClient
from .media import MediaService
from .ptz import PTZService

__all__ = [
    "ONVIFClient",
    "MediaService",
    "PTZService",
]
