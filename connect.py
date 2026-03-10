#!/usr/bin/env python3
"""
OpenClaw Relay Connector (Secure Channel Worker)

安全架构：本脚本只负责搬运纯文本聊天消息。
- 从中转服务器接收客户端消息
- 通过 OpenClaw CLI (`openclaw agent --session-id --message`) 安全地发送给 AI
- 使用 --session-id 绑定专属会话，自动维护上下文记忆
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
import re
import shutil
import uuid

import requests
import websockets

MAX_MESSAGE_LENGTH = 50000
OPENCLAW_CLI = os.getenv("OPENCLAW_CLI", "openclaw")
DEFAULT_SESSION_LABEL = os.getenv("OPENCLAW_SESSION_LABEL", "mobile-app")
VALID_EMOTIONS = {"speechless", "angry", "shy", "sad", "happy", "neutral"}

EMOTION_PROMPT = (
    '你现在是一个桌面宠物，正在和主人实时语音对话。'
    '你的每句回复都会被TTS朗读出来，所以必须简短口语化。\n'
    '回复要求：用一句话回答，20-30字中文最佳，不许超过50字中文。像朋友聊天一样说话，不要写长段落。\n'
    '输出格式（严格JSON，不要输出其他任何内容）：\n'
    '{{"emotion":"<happy|sad|angry|shy|speechless|neutral>","text":"你的简短回复"}}\n\n'
    '主人说：{message}'
)


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


async def call_openclaw_cli(message: str, label: str = DEFAULT_SESSION_LABEL, timeout: float = 120) -> str:
    """
    Send a message via the official CLI with a dedicated session ID.
    The --session-id ensures all messages from this client share the same
    conversation context, while staying isolated from the main terminal session.
    """
    if len(message) > MAX_MESSAGE_LENGTH:
        return "[Error] 消息过长"

    cli_path = shutil.which(OPENCLAW_CLI)
    if not cli_path:
        return f"[Error] 找不到 {OPENCLAW_CLI} 命令，请确保 OpenClaw CLI 已安装并在 PATH 中"

    try:
        proc = await asyncio.create_subprocess_exec(
            cli_path, "agent", "--session-id", label, "--message", message,
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


def strip_thinking(raw: str) -> str:
    """Remove AI thinking blocks from the reply."""
    import re as _re

    result = _re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=_re.IGNORECASE)
    result = _re.sub(r"<thinking>[\s\S]*?</thinking>", "", result, flags=_re.IGNORECASE)

    lines = result.split("\n")
    cleaned = []
    in_think = False
    for line in lines:
        stripped = line.strip()
        if (
            stripped.startswith("> think")
            or stripped == "<think>"
            or stripped == "<thinking>"
            or _re.match(r"^>\s*\*\*Thinking", stripped, _re.IGNORECASE)
            or _re.match(r"^Thinking Process:", stripped, _re.IGNORECASE)
        ):
            in_think = True
            continue
        if in_think:
            if stripped in ("</think>", "</thinking>", "---"):
                in_think = False
                continue
            if stripped.startswith(">") or stripped.startswith("**") or stripped == "":
                continue
            in_think = False
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def parse_reply(raw: str) -> tuple[str, str]:
    """
    Extract emotion and text from AI reply.
    Supports: {"emotion":"happy","text":"..."} or (happy) text
    Returns (emotion, clean_text).
    """
    text = strip_thinking(raw)
    if not text:
        return "neutral", "[Empty response]"

    # Try JSON: {"emotion": "...", "text": "..."}
    try:
        json_start = text.index("{")
        json_candidate = text[json_start:]
        brace_depth = 0
        json_end = json_start
        for i, ch in enumerate(json_candidate):
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    json_end = i + 1
                    break
        obj = json.loads(json_candidate[:json_end])
        emo = str(obj.get("emotion", "neutral")).lower().strip()
        t = str(obj.get("text", "")).strip()
        if t:
            return emo if emo in VALID_EMOTIONS else "neutral", t
    except (json.JSONDecodeError, ValueError):
        pass

    # Try legacy: (emotion) text
    m = re.match(r"^\((.*?)\)\s*(.*)", text, re.DOTALL)
    if m:
        emo = m.group(1).lower().strip()
        t = m.group(2).strip()
        if t and emo in VALID_EMOTIONS:
            return emo, t

    return "neutral", text


async def run(relay_url: str, link_code: str, secret: str, label: str):
    print(f"[*] 绑定到中转服务器: {relay_url}")
    result = do_link(relay_url, link_code, secret)
    token = result["token"]
    app_id = result["app_id"]
    print(f"[OK] 绑定成功，App ID: {app_id}")

    cli_path = shutil.which(OPENCLAW_CLI)
    if cli_path:
        print(f"[OK] OpenClaw CLI: {cli_path}")
        print(f"[OK] 会话标签: {label}")
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
            wrapped = EMOTION_PROMPT.format(message=content)
            raw_reply = await call_openclaw_cli(wrapped, label=label)
        else:
            raw_reply = f"[Echo] {content}"

        emotion, clean_text = parse_reply(raw_reply)

        await relay_ws.send(json.dumps({
            "type": "message",
            "content": clean_text,
            "content_type": "text",
            "emotion": emotion,
            "msg_id": str(uuid.uuid4()),
        }))
        print(f"[->] ({emotion}) {clean_text[:100]}{'...' if len(clean_text) > 100 else ''}")

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
    parser.add_argument("--label", default=DEFAULT_SESSION_LABEL,
                        help=f"OpenClaw 会话标签，用于隔离上下文（默认: {DEFAULT_SESSION_LABEL}）")
    args = parser.parse_args()

    print("=" * 50)
    print("  OpenClaw Relay Connector (Secure)")
    print("=" * 50)
    print(f"  中转服务器: {args.relay}")
    print(f"  Link Code:  {args.link_code}")
    print(f"  会话标签:   {args.label}")
    print(f"  模式:       CLI (安全隔离)")
    print("=" * 50 + "\n")

    asyncio.run(run(args.relay, args.link_code, args.secret, args.label))


if __name__ == "__main__":
    main()
