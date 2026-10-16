"""GB28181 RTP 流接收。

处理设备通过 RTP/RTCP 推送的媒体流：
- RTP 包接收与解析
- PS/RTP-H264 解封装
- 帧重组与时间戳处理

依赖 FFmpeg（通过 imageio-ffmpeg 或系统 ffmpeg）进行 PS 解封装。
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import Any, Callable

from loguru import logger


@dataclass
class RTPPacket:
    """RTP 包。"""

    version: int
    padding: bool
    extension: bool
    csrc_count: int
    marker: bool
    payload_type: int
    sequence_number: int
    timestamp: int
    ssrc: int
    payload: bytes


class RTPStreamHandler:
    """RTP 流处理器。

    从 UDP socket 接收 RTP 包，解析并重组为视频帧。
    """

    def __init__(self, port: int):
        self.port = port
        self._transport: asyncio.DatagramTransport | None = None
        self._running = False

        # 回调
        self.on_frame: Callable[[bytes, int], Any] | None = None  # (frame_data, timestamp)

        # 状态
        self._last_seq = -1
        self._frame_buffer = bytearray()
        self._current_ssrc: int | None = None

    async def start(self) -> None:
        """启动 RTP 接收。"""
        if self._running:
            return

        loop = asyncio.get_event_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: RTPProtocol(self),
            local_addr=("0.0.0.0", self.port),
        )
        self._running = True
        logger.info(f"RTP 流监听启动: 0.0.0.0:{self.port}")

    async def stop(self) -> None:
        """停止 RTP 接收。"""
        if self._transport:
            self._transport.close()
        self._running = False
        logger.info(f"RTP 流监听停止: {self.port}")

    def handle_packet(self, data: bytes) -> None:
        """处理接收到的 RTP 包。"""
        if len(data) < 12:
            return

        packet = self._parse_rtp(data)
        if packet is None:
            return

        # 检测丢包
        if self._last_seq >= 0:
            expected = (self._last_seq + 1) % 65536
            if packet.sequence_number != expected:
                lost = packet.sequence_number - self._last_seq
                if lost > 1:
                    logger.warning(f"RTP 丢包: 期望 seq={expected}, 实际={packet.sequence_number}")
                    self._frame_buffer.clear()  # 丢包则丢弃当前帧缓存
        self._last_seq = packet.sequence_number

        # 累积 payload
        self._frame_buffer.extend(packet.payload)

        # 标记位表示一帧结束
        if packet.marker:
            if self.on_frame and self._frame_buffer:
                try:
                    frame_data = bytes(self._frame_buffer)
                    self.on_frame(frame_data, packet.timestamp)
                except Exception as e:
                    logger.error(f"帧回调失败: {e}")
            self._frame_buffer.clear()

    @staticmethod
    def _parse_rtp(data: bytes) -> RTPPacket | None:
        """解析 RTP 包。"""
        try:
            byte0 = data[0]
            version = (byte0 >> 6) & 0x03
            if version != 2:
                return None

            padding = bool(byte0 & 0x20)
            extension = bool(byte0 & 0x10)
            csrc_count = byte0 & 0x0F

            byte1 = data[1]
            marker = bool(byte1 & 0x80)
            payload_type = byte1 & 0x7F

            sequence_number = struct.unpack("!H", data[2:4])[0]
            timestamp = struct.unpack("!I", data[4:8])[0]
            ssrc = struct.unpack("!I", data[8:12])[0]

            offset = 12 + csrc_count * 4
            if extension:
                ext_len = struct.unpack("!H", data[offset + 2 : offset + 4])[0]
                offset += 4 + ext_len * 4

            payload = data[offset:]
            if padding and payload:
                payload = payload[: -payload[-1]]

            return RTPPacket(
                version=version,
                padding=padding,
                extension=extension,
                csrc_count=csrc_count,
                marker=marker,
                payload_type=payload_type,
                sequence_number=sequence_number,
                timestamp=timestamp,
                ssrc=ssrc,
                payload=payload,
            )
        except Exception:
            return None


class RTPProtocol(asyncio.DatagramProtocol):
    """RTP UDP 协议。"""

    def __init__(self, handler: RTPStreamHandler):
        self.handler = handler

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.handler.handle_packet(data)

    def error_received(self, exc: Exception) -> None:
        logger.error(f"RTP 协议错误: {exc}")


class PSStreamDemuxer:
    """PS（Program Stream）解封装器。

    GB28181 媒体流通常是 PS 封装（MPEG-PS），内含 H.264 视频帧。
    使用 FFmpeg 进行解封装。
    """

    def __init__(self):
        self._ffmpeg_process: asyncio.subprocess.Process | None = None
        self._buffer = bytearray()

    async def start(self) -> None:
        """启动 FFmpeg 解封装进程。"""
        import imageio_ffmpeg

        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

        self._ffmpeg_process = await asyncio.create_subprocess_exec(
            ffmpeg_path,
            "-f", "mpegts",  # 或 ps，取决于实际流格式
            "-i", "pipe:0",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", "640x480",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info("PS 解封装器启动")

    async def feed(self, data: bytes) -> None:
        """送入 PS/RTP 数据。"""
        if self._ffmpeg_process and self._ffmpeg_process.stdin:
            self._ffmpeg_process.stdin.write(data)
            await self._ffmpeg_process.stdin.drain()

    async def read_frame(self) -> bytes | None:
        """读取一帧 BGR24 数据。"""
        if not self._ffmpeg_process or not self._ffmpeg_process.stdout:
            return None
        frame_size = 640 * 480 * 3
        return await self._ffmpeg_process.stdout.read(frame_size)

    async def stop(self) -> None:
        """停止解封装。"""
        if self._ffmpeg_process:
            try:
                self._ffmpeg_process.kill()
                await self._ffmpeg_process.wait()
            except Exception:
                pass
            self._ffmpeg_process = None
