#!/usr/bin/env python3
"""
OpenClaw Relay Connector (Secure Channel Worker)

安全架构：本脚本只负责搬运纯文本聊天消息。
- 从中转服务器接收客户端消息
- 通过 OpenClaw CLI (`openclaw agent --message`) 安全地发送给 AI
- 将 AI 的纯文本回复推回中转服务器

不直接连接 Gateway WebSocket，不持有任何系统级权限。
即使中转服务器被攻破，攻击者最多只能发送聊天文本，无法执行命令。

用法:
    python3 -u connect.py --relay https://xxx.up.railway.app --link-code A7X9K2 --secret f3a8b1c2d4e5
"""

import argparse
import asyncio
import json
import os
import shutil
import uuid

import requests
import websockets

MAX_MESSAGE_LENGTH = 50000
OPENCLAW_CLI = os.getenv("OPENCLAW_CLI", "openclaw")


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


async def call_openclaw_cli(message: str, timeout: float = 120) -> str:
    """
    Through the official CLI, send a message as a normal user chat.
    This is the security boundary: the CLI handles permissions internally,
    and this script never touches the Gateway or any system-level API.
    """
    if len(message) > MAX_MESSAGE_LENGTH:
        return "[Error] 消息过长"

    cli_path = shutil.which(OPENCLAW_CLI)
    if not cli_path:
        return f"[Error] 找不到 {OPENCLAW_CLI} 命令，请确保 OpenClaw CLI 已安装并在 PATH 中"

    try:
        proc = await asyncio.create_subprocess_exec(
            cli_path, "agent", "--message", message,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        if proc.returncode != 0:
            err = stderr.decode().strip() or f"exit code {proc.returncode}"
            return f"[Error] CLI 调用失败: {err}"

        return stdout.decode().strip() or "[Empty response]"
    except asyncio.TimeoutError:
        proc.kill()
        return "[Error] AI 响应超时"
    except Exception as e:
        return f"[Error] {e}"


async def run(relay_url: str, link_code: str, secret: str):
    print(f"[*] 绑定到中转服务器: {relay_url}")
    result = do_link(relay_url, link_code, secret)
    token = result["token"]
    app_id = result["app_id"]
    print(f"[OK] 绑定成功，App ID: {app_id}")

    cli_path = shutil.which(OPENCLAW_CLI)
    if cli_path:
        print(f"[OK] OpenClaw CLI: {cli_path}")
    else:
        print(f"[!] 警告: 找不到 '{OPENCLAW_CLI}' 命令，将以 echo 模式运行")
        print(f"[!] 请安装 OpenClaw CLI 或设置 OPENCLAW_CLI 环境变量\n")

    ws_url = relay_url.replace("https://", "wss://").replace("http://", "ws://")
    relay_ws_url = f"{ws_url}/ws/openclaw?token={token}"

    async def handle_message(relay_ws, content, sender):
        if not isinstance(content, str) or len(content) > MAX_MESSAGE_LENGTH:
            print(f"[!] 丢弃非法消息 from {sender}")
            return

        print(f"[<-] {sender}: {content}")

        if cli_path:
            reply = await call_openclaw_cli(content)
        else:
            reply = f"[Echo] {content}"

        await relay_ws.send(json.dumps({
            "type": "message",
            "content": reply,
            "content_type": "text",
            "msg_id": str(uuid.uuid4()),
        }))
        print(f"[->] {reply[:100]}{'...' if len(reply) > 100 else ''}")

    backoff = 1
    while True:
        try:
            print(f"[*] 连接中转服务器...")
            async with websockets.connect(relay_ws_url, ping_interval=None) as relay_ws:
                print(f"[OK] 已连接，等待客户端消息...\n")
                backoff = 1
                pending_tasks: set[asyncio.Task] = set()

                async for raw in relay_ws:
                    msg = json.loads(raw)
                    msg_type = msg.get("type")

                    if msg_type == "ping":
                        await relay_ws.send(json.dumps({"type": "pong"}))
                        continue

                    if msg_type == "message":
                        content = msg.get("content", "")
                        sender = msg.get("from", "unknown")
                        task = asyncio.create_task(handle_message(relay_ws, content, sender))
                        pending_tasks.add(task)
                        task.add_done_callback(pending_tasks.discard)

                    done = {t for t in pending_tasks if t.done()}
                    pending_tasks -= done

        except websockets.ConnectionClosed:
            print(f"\n[!] 断开，{backoff:.0f}s 后重连...")
        except Exception as e:
            print(f"\n[!] 错误: {e}，{backoff:.0f}s 后重连...")

        await asyncio.sleep(backoff)
        backoff = min(backoff * 1.5, 30)


def main():
    parser = argparse.ArgumentParser(description="将你的 OpenClaw 连接到中转服务器（安全模式）")
    parser.add_argument("--relay", required=True, help="中转服务器地址")
    parser.add_argument("--link-code", required=True, help="客户端给的 Link Code")
    parser.add_argument("--secret", required=True, help="客户端给的 Secret")
    args = parser.parse_args()

    print("=" * 50)
    print("  OpenClaw Relay Connector (Secure)")
    print("=" * 50)
    print(f"  中转服务器: {args.relay}")
    print(f"  Link Code:  {args.link_code}")
    print(f"  模式:       CLI (安全隔离)")
    print("=" * 50 + "\n")

    asyncio.run(run(args.relay, args.link_code, args.secret))


if __name__ == "__main__":
    main()
