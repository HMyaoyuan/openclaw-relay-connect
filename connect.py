#!/usr/bin/env python3
"""
OpenClaw Relay Connector

用户的 OpenClaw 运行此脚本，用客户端给的 link_code + secret 绑定并连接中转服务器。
通过 OpenClaw Gateway WebSocket 协议转发消息，支持上下文记忆和流式输出。

用法:
    python3 -u connect.py --relay https://xxx.up.railway.app --link-code A7X9K2 --secret f3a8b1c2d4e5
"""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import time
import uuid

import requests
import websockets

OPENCLAW_GATEWAY_URL = os.getenv("OPENCLAW_GATEWAY_URL", "ws://127.0.0.1:18789")
OPENCLAW_GATEWAY_TOKEN = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")


def do_link(relay_url: str, link_code: str, secret: str) -> dict:
    res = requests.post(
        f"{relay_url}/api/link",
        json={"link_code": link_code, "secret": secret},
        timeout=10,
    )
    if not res.ok:
        try:
            detail = res.json().get("detail", res.text)
        except Exception:
            detail = res.text
        raise Exception(f"绑定失败: {detail}")
    return res.json()


# ─── Ed25519 Device Identity ──────────────────────────────────────────────────

def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


class DeviceIdentity:
    def __init__(self):
        from nacl.signing import SigningKey
        self._signing_key = SigningKey.generate()
        self._verify_key = self._signing_key.verify_key
        pub_bytes = bytes(self._verify_key)
        self.public_key = b64url_encode(pub_bytes)
        self.private_key = b64url_encode(bytes(self._signing_key))
        self.device_id = hashlib.sha256(pub_bytes).hexdigest()

    def sign(self, payload: str) -> str:
        sig = self._signing_key.sign(payload.encode())
        return b64url_encode(sig.signature)


def build_auth_payload(
    device_id: str, client_id: str, client_mode: str,
    role: str, scopes: list[str], signed_at_ms: int,
    token: str, nonce: str,
) -> str:
    return "|".join([
        "v2", device_id, client_id, client_mode,
        role, ",".join(scopes), str(signed_at_ms),
        token, nonce,
    ])


# ─── Gateway Client ──────────────────────────────────────────────────────────

class GatewayClient:
    def __init__(self, gateway_url: str, gateway_token: str):
        self.gateway_url = gateway_url
        self.gateway_token = gateway_token
        self._ws = None
        self._connected = False
        self._pending = {}
        self._seq = 0
        self._device = DeviceIdentity()
        self._event_handler = None

    async def connect(self):
        self._ws = await websockets.connect(self.gateway_url, ping_interval=None)

        raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
        challenge = json.loads(raw)
        if challenge.get("type") != "event" or challenge.get("event") != "connect.challenge":
            raise Exception(f"Expected connect.challenge, got: {challenge}")

        challenge_payload = challenge.get("payload", {})
        nonce = challenge_payload.get("nonce", "")
        signed_at_ms = challenge_payload.get("ts", int(time.time() * 1000))

        role = "operator"
        scopes = [
            "operator.read", "operator.write", "operator.admin",
            "operator.approvals", "operator.pairing",
        ]
        client_id = "cli"
        client_mode = "cli"

        signing_token = self.gateway_token or ""
        payload = build_auth_payload(
            self._device.device_id, client_id, client_mode,
            role, scopes, signed_at_ms, signing_token, nonce,
        )
        signature = self._device.sign(payload)

        auth = {}
        if self.gateway_token:
            auth["token"] = self.gateway_token

        self._seq += 1
        connect_msg = {
            "type": "req",
            "id": f"conn-{self._seq}",
            "method": "connect",
            "params": {
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": {
                    "id": client_id,
                    "version": "1.0.0",
                    "platform": "python",
                    "mode": client_mode,
                },
                "role": role,
                "scopes": scopes,
                "auth": auth,
                "device": {
                    "id": self._device.device_id,
                    "publicKey": self._device.public_key,
                    "signature": signature,
                    "signedAt": signed_at_ms,
                    "nonce": nonce,
                },
                "locale": "zh-CN",
                "userAgent": "openclaw-relay-connector/1.0.0",
                "caps": ["tool-events"],
            },
        }
        await self._ws.send(json.dumps(connect_msg))

        raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
        resp = json.loads(raw)
        if not resp.get("ok"):
            err = resp.get("error", {})
            raise Exception(f"Gateway rejected: {err.get('message', resp)}")

        resp_payload = resp.get("payload", {})
        auth_data = resp_payload.get("auth", {})
        device_token = auth_data.get("deviceToken")
        if device_token:
            self._device_token = device_token
        self._connected = True

    async def send_chat(self, message: str, session_key: str = "main") -> dict:
        self._seq += 1
        req_id = f"chat-{self._seq}"
        fut = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        await self._ws.send(json.dumps({
            "type": "req",
            "id": req_id,
            "method": "chat.send",
            "params": {"message": message, "sessionKey": session_key},
        }))
        return await asyncio.wait_for(fut, timeout=120)

    async def recv_loop(self):
        async for raw in self._ws:
            msg = json.loads(raw)
            if msg.get("type") == "res":
                fut = self._pending.pop(msg.get("id"), None)
                if fut and not fut.done():
                    fut.set_result(msg)
            elif msg.get("type") == "event" and self._event_handler:
                await self._event_handler(msg)

    async def close(self):
        if self._ws:
            await self._ws.close()


# ─── Main ─────────────────────────────────────────────────────────────────────

async def run(relay_url: str, link_code: str, secret: str, gateway_url: str, gateway_token: str):
    print(f"[*] 绑定到中转服务器: {relay_url}")
    result = do_link(relay_url, link_code, secret)
    token = result["token"]
    app_id = result["app_id"]
    print(f"[OK] 绑定成功，App ID: {app_id}")

    gateway = None
    if gateway_url:
        try:
            print(f"[*] 连接本地 Gateway: {gateway_url}")
            gateway = GatewayClient(gateway_url, gateway_token)
            await gateway.connect()
            print(f"[OK] 已连接到 Gateway")
            asyncio.create_task(gateway.recv_loop())
        except Exception as e:
            print(f"[!] 无法连接 Gateway: {e}")
            print(f"[!] 将以 echo 模式运行\n")
            gateway = None
    else:
        print(f"[*] 模式: Echo（未配置 Gateway）\n")

    ws_url = relay_url.replace("https://", "wss://").replace("http://", "ws://")
    relay_ws_url = f"{ws_url}/ws/openclaw?token={token}"

    backoff = 1
    while True:
        try:
            print(f"[*] 连接中转服务器...")
            async with websockets.connect(relay_ws_url) as relay_ws:
                print(f"[OK] 已连接，等待客户端消息...\n")
                backoff = 1
                async for raw in relay_ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "ping":
                        await relay_ws.send(json.dumps({"type": "pong"}))
                        continue

                    if msg.get("type") == "message":
                        content = msg.get("content", "")
                        sender = msg.get("from", "unknown")
                        print(f"[<-] {sender}: {content}")

                        if gateway and gateway._connected:
                            try:
                                result = await gateway.send_chat(content)
                                if result.get("ok"):
                                    p = result.get("payload", {})
                                    reply = p.get("message", p.get("content", json.dumps(p)))
                                else:
                                    err = result.get("error", {})
                                    reply = f"[Gateway Error] {err.get('message', str(err))}"
                            except Exception as e:
                                reply = f"[Error] {e}"
                        else:
                            reply = f"[Echo] {content}"

                        await relay_ws.send(json.dumps({
                            "type": "message",
                            "content": reply,
                            "content_type": "text",
                            "msg_id": str(uuid.uuid4()),
                        }))
                        print(f"[->] {reply[:100]}{'...' if len(reply) > 100 else ''}")

        except websockets.ConnectionClosed:
            print(f"\n[!] 断开，{backoff:.0f}s 后重连...")
        except Exception as e:
            print(f"\n[!] 错误: {e}，{backoff:.0f}s 后重连...")

        await asyncio.sleep(backoff)
        backoff = min(backoff * 1.5, 30)


def main():
    parser = argparse.ArgumentParser(description="将你的 OpenClaw 连接到中转服务器")
    parser.add_argument("--relay", required=True, help="中转服务器地址")
    parser.add_argument("--link-code", required=True, help="客户端给的 Link Code")
    parser.add_argument("--secret", required=True, help="客户端给的 Secret")
    parser.add_argument("--gateway", default=OPENCLAW_GATEWAY_URL, help="Gateway 地址")
    parser.add_argument("--gateway-token", default=OPENCLAW_GATEWAY_TOKEN, help="Gateway Token")
    parser.add_argument("--echo", action="store_true", help="强制 echo 模式")
    args = parser.parse_args()

    gw_url = "" if args.echo else args.gateway
    gw_token = args.gateway_token

    print("=" * 50)
    print("  OpenClaw Relay Connector")
    print("=" * 50)
    print(f"  中转服务器: {args.relay}")
    print(f"  Link Code: {args.link_code}")
    print(f"  Gateway:   {gw_url or '(echo 模式)'}")
    print("=" * 50 + "\n")

    asyncio.run(run(args.relay, args.link_code, args.secret, gw_url, gw_token))


if __name__ == "__main__":
    main()
