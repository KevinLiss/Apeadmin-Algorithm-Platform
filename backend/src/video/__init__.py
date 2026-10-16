"""视频接入模块。

支持多种视频协议：
- RTSP/RTP：传统摄像头实时流
- GB28181：国标协议设备接入
- ONVIF：网络视频接口标准

使用示例：
```python
from src.video import VideoFactory, CameraConfig

# 根据摄像头配置创建视频源
config = CameraConfig(camera_id=1, name="大门摄像头", protocol="rtsp", rtsp_url="rtsp://...")
source = await VideoFactory.create_source(config)
print(source.stream_url)
```
"""

from .factory import CameraConfig, VideoFactory, VideoProtocol, VideoSource
from .api import router as video_router

__all__ = [
    "CameraConfig",
    "VideoFactory",
    "VideoProtocol",
    "VideoSource",
    "video_router",
]
