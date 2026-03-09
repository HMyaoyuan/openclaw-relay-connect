#!/usr/bin/env python3
"""
OpenClaw Relay Connector

用户的 OpenClaw 运行此脚本，用客户端给的 link_code + secret 绑定并连接中转服务器。

用法:
    npx openclaw-relay-connect --relay https://xxx.up.railway.app --link-code A7X9K2 --secret f3a8b1c2d4e5
"""

import argparse
import asyncio
import json
import os
import sys
import uuid

import requests
import websockets

OPENCLAW_GATEWAY_URL = os.getenv("OPENCLAW_GATEWAY_URL", "ws://127.0.0.1:18789")
OPENCLAW_GATEWAY_TOKEN = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")


def do_link(relay_url: str, link_code: str, secret: str) -> dict:
    """Call /api/link to bind and get a token."""
    res = requests.post(
        f"{relay_url}/api/link",
        json={"link_code": link_code, "secret": secret},
        timeout=10,
    )
    if not res.ok:
        detail = res.json().get("detail", res.text) if res.headers.get("content-type", "").startswith("application/json") else res.text
        raise Exception(f"绑定失败: {detail}")
    return res.json()


class GatewayClient:
    def __init__(self, gateway_url: str, gateway_token: str):
        self.gateway_url = gateway_url
        self.gateway_token = gateway_token
        self._ws = None
        self._connected = False
        self._pending = {}
        self._seq = 0

    async def connect(self):
        self._ws = await websockets.connect(self.gateway_url)
        raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
        challenge = json.loads(raw)
        if challenge.get("event") != "connect.challenge":
            raise Exception(f"Expected connect.challenge, got: {challenge}")

        self._seq += 1
        await self._ws.send(json.dumps({
            "type": "req", "id": f"conn-{self._seq}", "method": "connect",
            "params": {
                "minProtocol": 3, "maxProtocol": 3,
                "client": {"id": "relay-connector", "version": "1.0.0", "platform": sys.platform, "mode": "operator"},
                "role": "operator", "scopes": ["operator.read", "operator.write"],
                "caps": [], "commands": [], "permissions": {},
                "auth": {"token": self.gateway_token} if self.gateway_token else {},
                "locale": "zh-CN", "userAgent": "openclaw-relay-connector/1.0.0",
            },
        }))

        raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
        resp = json.loads(raw)
        if not resp.get("ok"):
            raise Exception(f"Gateway rejected: {resp}")
        self._connected = True

    async def send_chat(self, message: str, session_key: str = "main") -> dict:
        self._seq += 1
        req_id = f"chat-{self._seq}"
        fut = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        await self._ws.send(json.dumps({
            "type": "req", "id": req_id, "method": "chat.send",
            "params": {"message": message, "sessionKey": session_key},
        }))
        return await asyncio.wait_for(fut, timeout=60)

    async def listen(self, on_event):
        async for raw in self._ws:
            msg = json.loads(raw)
            if msg.get("type") == "res":
                fut = self._pending.pop(msg.get("id"), None)
                if fut and not fut.done():
                    fut.set_result(msg)
            elif msg.get("type") == "event" and on_event:
                await on_event(msg)

    async def close(self):
        if self._ws:
            await self._ws.close()


async def run(relay_url: str, link_code: str, secret: str, gateway_url: str, gateway_token: str):
    print(f"[*] 绑定到中转服务器: {relay_url}")
    result = do_link(relay_url, link_code, secret)
    token = result["token"]
    app_id = result["app_id"]
    print(f"[OK] 绑定成功，App ID: {app_id}")

    gateway = None
    if gateway_url and gateway_token:
        try:
            print(f"[*] 连接本地 Gateway: {gateway_url}")
            gateway = GatewayClient(gateway_url, gateway_token)
            await gateway.connect()
            print(f"[OK] 已连接到 Gateway")
        except Exception as e:
            print(f"[!] 无法连接 Gateway: {e}")
            print(f"[!] 将以 echo 模式运行\n")
            gateway = None

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
                                text = ""
                                if result.get("ok"):
                                    p = result.get("payload", {})
                                    text = p.get("message", p.get("content", json.dumps(p)))
                                else:
                                    text = f"[Gateway Error] {result.get('error', 'unknown')}"
                                await relay_ws.send(json.dumps({
                                    "type": "message", "content": text,
                                    "content_type": "text", "msg_id": str(uuid.uuid4()),
                                }))
                                print(f"[->] {text[:80]}...")
                            except Exception as e:
                                await relay_ws.send(json.dumps({
                                    "type": "message", "content": f"[Error] {e}",
                                    "content_type": "text", "msg_id": str(uuid.uuid4()),
                                }))
                        else:
                            await relay_ws.send(json.dumps({
                                "type": "message", "content": f"[Echo] {content}",
                                "content_type": "text", "msg_id": str(uuid.uuid4()),
                            }))
                            print(f"[->] Echo")

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
    parser.add_argument("--gateway", default=OPENCLAW_GATEWAY_URL, help="本地 Gateway 地址")
    parser.add_argument("--gateway-token", default=OPENCLAW_GATEWAY_TOKEN, help="Gateway Token")
    args = parser.parse_args()

    print("=" * 50)
    print("  OpenClaw Relay Connector")
    print("=" * 50)
    print(f"  中转服务器: {args.relay}")
    print(f"  Link Code: {args.link_code}")
    print(f"  Gateway:   {args.gateway_token and args.gateway or '(echo 模式)'}")
    print("=" * 50 + "\n")

    gw_url = args.gateway if args.gateway_token else ""
    asyncio.run(run(args.relay, args.link_code, args.secret, gw_url, args.gateway_token))


if __name__ == "__main__":
    main()
