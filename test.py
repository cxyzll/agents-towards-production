import os
import json
import locale
import subprocess
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(override=True)

try:
    import readline
    readline.parse_and_bind('set bind-tty-special-chars off')
    readline.parse_and_bind('set input-meta on')
    readline.parse_and_bind('set output-meta on')
    readline.parse_and_bind('set convert-meta off')
except ImportError:
    pass


if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)


WORKDIR = Path.cwd()

client = OpenAI(
    base_url = os.getenv("OPENAI_API_URL"),
    api_key = os.getenv("OPENAI_API_KEY"),
)

MODEL = 'gpt-5.6-luna'

SYSTEM = f"你是{WORKDIR}下的AI智能助手，所有具有破坏性的操作都需要获得用户批准。"

# -- tools --

def decode_subprocess_output(data: bytes | None) -> str:
    if not data:
        return ""

    encodings = ("utf-8", locale.getpreferredencoding(False), "gb18030")
    for encoding in dict.fromkeys(encodings):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    return data.decode("utf-8", errors="replace")


def run_bash(command: str) -> str:
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, timeout=120)
        stdout = decode_subprocess_output(r.stdout)
        stderr = decode_subprocess_output(r.stderr)
        out = (stdout + stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def run_read(path: str, limit: int | None = None) -> str:
    try:
        lines = (WORKDIR / path).resolve().read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = (WORKDIR / path).resolve()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = (WORKDIR / path).resolve()
        text = file_path.read_text()
        if old_text not in text:
            return f"Error: text not found in {path}"
        file_path.write_text(text.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


def run_glob(pattern: str) -> str:
    import glob as g
    try:
        results = []
        for match in g.glob(pattern, root_dir=WORKDIR):
            if (WORKDIR / match).resolve().is_relative_to(WORKDIR):
                results.append(match)
        return "\n".join(results) if results else "(no matches)"
    except Exception as e:
        return f"Error: {e}"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "执行一条shell命令。",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "将内容写入文件。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "一次性替换文件中的精确文本。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "查找匹配通配符模式的文件。",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
]

TOOL_HANDLERS = {
    "bash": run_bash,
    "read_file": run_read,
    "write_file": run_write,
    "edit_file": run_edit,
    "glob": run_glob,
}

# 通道1：硬性拒绝名单——永久禁止
DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if=", "> /dev/sda"]

def check_deny_list(command: str) -> str | None:
    for pattern in DENY_LIST:
        if pattern in command:
            return f"已阻止：'{pattern}'位于拒绝列表中"
    return None


# 2号关卡：规则匹配——依赖上下文的检查

PERMISSION_RULES = [
    {
        "tools":[
            "read_file","write_file","edit_file"
        ],
        "check": lambda args: not (WORKDIR / args.get("path","")).resolve().is_relative_to(WORKDIR),
        "message":"工作区外写入"
    },
    {
        "tools":['bash'],
        "check": lambda args: any(kw in args.get("command","") for kw in ["rm ", "> /etc/", "chmod 777"]),
        "message":"具有潜在破坏性的命令"
    }
]

def check_rules(tool_name:str, args:dict)->str | None:
    for rule in PERMISSION_RULES:
        if tool_name in rule["tools"] and rule["check"](args):
            return rule["message"]
    return None

# 关卡3：用户审批 — 规则匹配后等待确认
def ask_user(tool_name:str,args: dict, reason: str) -> str:
    print(f"\n\033[33m[permission] {reason}\033[0m")
    print(f"   Tool: {tool_name}({args})")
    choice = input("   Allow? [y/N] ").strip().lower()
    return "allow" if choice in ("y", "yes") else "deny"

# 流水线：三道门控串联
def check_permission(tool_name: str, args: dict) -> bool:
    if tool_name == "bash":
        reason = check_deny_list(args.get("command", ""))
        if reason:
            print(f"\n\033[31m[blocked] {reason}\033[0m")
            return False

    reason = check_rules(tool_name, args)
    if reason:
        decision = ask_user(tool_name, args, reason)
        if decision == "deny":
            return False

    return True


# -- 智能体循环：与s02一致，插入了check_permission()函数 --
def agent_loop(messages: list):
    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                *messages,
            ],
            tools=TOOLS,
            max_tokens=8000,
        )

        choice = response.choices[0]
        message = choice.message

        # 打印模型的文本输出
        if message.content:
            print(f"\n\033[32mAI: {message.content}\033[0m")

        print(message.model_dump_json(indent=2))
        # 保存完整 assistant 消息，包括 tool_calls
        messages.append(message.model_dump(exclude_none=True))

        if choice.finish_reason != "tool_calls" or not message.tool_calls:
            return

        for tool_call in message.tool_calls:
            if tool_call.type != "function":
                continue

            function = tool_call.function
            tool_name = function.name

            # OpenAI 返回的 arguments 是 JSON 字符串
            args = json.loads(function.arguments)

            print(f"\033[36m> {tool_name}\033[0m")

            if not check_permission(tool_name, args):
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": "Permission denied.",
                })
                continue

            handler = TOOL_HANDLERS.get(tool_name)
            output = (
                handler(**args)
                if handler
                else f"Unknown: {tool_name}"
            )

            print(str(output)[:200])

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(output),
            })

if __name__ == "__main__":
    # print("s03: Permission")
    print("输入问题，按回车键发送。输入q即可退出。\n")

    history = []
    while True:
        try:
            query = input("\033[36ms03 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        response_content = history[-1]["content"]
        if response_content:
            print(response_content)
        print()
