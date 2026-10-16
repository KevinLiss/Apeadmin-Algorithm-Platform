"""GB28181 设备接入模块。

GB/T 28181-2016 国标协议支持：
- 设备注册（REGISTER）
- 设备注销（REGISTER with expires=0）
- 心跳保活（OPTIONS）
- 目录查询（CATALOG）
- 实时流订阅（INVITE -> ACK -> RTP）
- 告警订阅（ALARM）

使用示例：
```python
from src.video.gb28181 import GB28181Server, GB28181Device

# 创建 GB28181 服务器（平台侧）
server = GB28181Server(
    sip_id="31010000002000000001",  # 平台ID
    sip_domain="3101000000",
    sip_host="0.0.0.0",
    sip_port=5060,
)

# 启动服务器
await server.start()

# 处理设备注册
device = await server.register_device(device_id, ip, port)
```
"""

from .server import GB28181Server
from .device import GB28181Device, DeviceStatus
from .stream import RTPStreamHandler

__all__ = [
    "GB28181Server",
    "GB28181Device",
    "DeviceStatus",
    "RTPStreamHandler",
]
