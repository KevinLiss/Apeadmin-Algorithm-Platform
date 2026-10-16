"""Webhook 报警外发渠道（HTTP POST，零额外依赖）。

把告警快照以 JSON 形式 POST 到用户配置的回调 URL（企业微信 /
钉钉 / 飞书 / 自研告警网关均可）。使用标准库 ``http.client`` 实现，
不引入第三方 HTTP 客户端，避免与 FastAPI 的 httpx 事件循环耦合。

支持的配置项：
- ``url``：回调地址（必填）
- ``secret``：可选，作为 ``X-ApeAdmin-Signature`` 头（HMAC-SHA256 hex）
  便于接收方校验来源；为空则不签名。
- ``timeout``：请求超时秒数（默认 5）。
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import logging
import ssl
import urllib.parse
from typing import Any

from src.notification.base import NotificationChannel

logger = logging.getLogger("notification.webhook")


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class WebhookChannel(NotificationChannel):
    name = "webhook"

    def is_enabled(self) -> bool:
        if str(self.config.get("enabled", "false")).lower() != "true":
            return False
        url = (self.config.get("url") or "").strip()
        return url.startswith("http://") or url.startswith("https://")

    def send(self, payload: dict[str, Any]) -> bool:
        if not self.is_enabled():
            return False
        url = (self.config.get("url") or "").strip()
        try:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            parsed = urllib.parse.urlparse(url)
            host = parsed.hostname or ""
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "ApeAdmin-Notification/1.0",
                "Content-Length": str(len(body)),
            }
            secret = (self.config.get("secret") or "").strip()
            if secret:
                headers["X-ApeAdmin-Signature"] = _sign(secret, body)
            timeout = float(self.config.get("timeout") or 5)
            if parsed.scheme == "https":
                ctx = ssl.create_default_context()
                conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
            else:
                conn = http.client.HTTPConnection(host, port, timeout=timeout)
            conn.request("POST", path, body=body, headers=headers)
            resp = conn.getresponse()
            code = resp.status
            conn.close()
            ok = 200 <= code < 300
            if not ok:
                logger.warning(f"[Webhook] POST {url} 返回 {code}")
            return ok
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[Webhook] POST {url} 失败: {exc}")
            return False

    def test_connection(self) -> tuple[bool, str]:
        url = (self.config.get("url") or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            return False, "未配置合法的 webhook URL（需 http/https 开头）"
        test_payload = {"type": "TEST", "message": "ApeAdmin 通知连通性测试"}
        ok = self.send(test_payload)
        return ok, "连通性测试成功" if ok else "连通性测试失败（检查 URL/网络/接收端）"
