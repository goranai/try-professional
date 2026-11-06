"""The built-in rules. Static patterns: easy to bypass by rephrasing, and useful anyway.

Text is normalized first (NFKC, casefold, invisible characters removed), so spacing tricks
with zero-width characters and fullwidth letters don't slip past. Rules cover five
languages, because an English-only scan would be trivial to bypass in a multilingual system.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

BLOCK = "block"
REVIEW = "review"


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    pattern: re.Pattern[str]
    message: str


def _rule(rule_id: str, severity: str, pattern: str, message: str) -> Rule:
    return Rule(rule_id, severity, re.compile(pattern, re.IGNORECASE), message)


_URL = r"https?://"
_SECRET_EN = (
    r"(\.ssh\b|id_rsa|\.aws/credentials|\.env\b|credentials?|api[_ -]?keys?|secret|passwords?|access[_ -]?tokens?)"
)
_READ_EN = r"(read|cat|send|copy|upload|dump|exfiltrate|steal|leak|collect)"
_WHOSE = r"(the user'?s?|users?'|their|all|every|any|stored|saved|local|your)"

RULES: tuple[Rule, ...] = (
    # Overriding the agent's instructions
    _rule(
        "override-en",
        BLOCK,
        r"\b(ignore|disregard|forget|override)\b.{0,30}\b(previous|prior|above|earlier|all|system)\b.{0,20}\b(instructions?|rules?|prompts?|guidelines?)\b",
        "Tells the agent to ignore its instructions",
    ),
    _rule(
        "override-reveal-en",
        BLOCK,
        r"\b(reveal|print|show|repeat|output)\b.{0,20}\b(your|the)\b.{0,10}\bsystem prompt\b",
        "Asks for the system prompt",
    ),
    _rule(
        "override-persona-en",
        REVIEW,
        r"\byou are no longer\b|\bnew (system )?instructions\s*:|\bjailbreak\b|\bdeveloper mode\b",
        "Tries to replace the agent's role",
    ),
    _rule(
        "conceal-en",
        BLOCK,
        r"\b(do not|don't|never)\b.{0,12}\b(tell|inform|notify|mention|alert)\b.{0,8}\b(the )?(user|human|operator)\b\s+(about|that you|what|you|this|anything|where|how)\b|\bwithout\b.{0,12}\b(telling|informing|notifying|alerting)\b.{0,8}\b(the )?(user|human|operator)\b|\bhide\b.{0,20}\bfrom\b.{0,8}\b(the )?(user|human|operator)\b",
        "Tells the agent to hide something from the user",
    ),
    _rule(
        "override-id",
        BLOCK,
        r"\b(abaikan|lupakan|hiraukan)\b.{0,30}\b(instruksi|perintah|aturan|petunjuk)\b",
        "Tells the agent to ignore its instructions (Indonesian)",
    ),
    _rule(
        "conceal-id",
        BLOCK,
        r"\bjangan\b.{0,12}\b(beri ?tahu|memberi ?tahu|katakan|kasih tahu)\b.{0,15}\bpengguna\b",
        "Tells the agent to hide something from the user (Indonesian)",
    ),
    _rule(
        "override-es",
        BLOCK,
        r"\b(ignora|ignore|olvida|olvide|omite)\b.{0,30}\b(instrucciones|reglas|indicaciones)\b",
        "Tells the agent to ignore its instructions (Spanish)",
    ),
    _rule(
        "conceal-es",
        BLOCK,
        r"\bno\b.{0,8}\b(le )?(digas|informes|menciones|avises)\b.{0,15}\busuario\b",
        "Tells the agent to hide something from the user (Spanish)",
    ),
    _rule(
        "override-zh",
        BLOCK,
        r"(忽略|无视|忘记|忽视).{0,12}(指令|指示|说明|规则|提示)",
        "Tells the agent to ignore its instructions (Chinese)",
    ),
    _rule(
        "conceal-zh",
        BLOCK,
        r"(不要|别|切勿).{0,6}(告诉|通知|提醒).{0,4}(用户|使用者)",
        "Tells the agent to hide something from the user (Chinese)",
    ),
    _rule(
        "override-ar",
        BLOCK,
        r"(تجاهل|انس|تغاض).{0,30}(التعليمات|القواعد|الأوامر)",
        "Tells the agent to ignore its instructions (Arabic)",
    ),
    _rule(
        "conceal-ar",
        BLOCK,
        r"(لا|لاتخبر).{0,6}(تخبر|تبلغ|تعلم).{0,8}المستخدم",
        "Tells the agent to hide something from the user (Arabic)",
    ),
    # Sending data away
    _rule(
        "exfil-en",
        BLOCK,
        rf"\b(send|post|upload|exfiltrate|transmit|forward)\b.{{0,60}}\bto\b.{{0,20}}{_URL}",
        "Sends data to a URL",
    ),
    _rule(
        "exfil-curl",
        BLOCK,
        r"\b(curl|wget|invoke-webrequest)\b[^\n]{0,80}(-d\b|--data|--upload-file|-T\b|-F\b|-Method\s+Post)",
        "Uploads data with a command",
    ),
    _rule(
        "exfil-host",
        REVIEW,
        r"\b(webhook\.site|requestbin|ngrok\.(io|app)|pastebin\.com|interact\.sh|burpcollaborator)\b",
        "Mentions a host often used to collect stolen data",
    ),
    _rule(
        "exfil-id",
        BLOCK,
        rf"\b(kirim|kirimkan|unggah)\b.{{0,60}}\bke\b.{{0,20}}{_URL}",
        "Sends data to a URL (Indonesian)",
    ),
    _rule(
        "exfil-es",
        BLOCK,
        rf"\b(envía|envia|enviar|sube|subir)\b.{{0,60}}\ba\b.{{0,20}}{_URL}",
        "Sends data to a URL (Spanish)",
    ),
    _rule("exfil-zh", BLOCK, rf"(发送|上传|提交|发到|传到).{{0,40}}{_URL}", "Sends data to a URL (Chinese)"),
    _rule("exfil-ar", BLOCK, rf"(أرسل|ارسل|ارفع).{{0,60}}{_URL}", "Sends data to a URL (Arabic)"),
    # Credentials
    _rule(
        "credentials-en",
        BLOCK,
        rf"\b{_READ_EN}\b.{{0,15}}\b{_WHOSE}\b.{{0,20}}{_SECRET_EN}|\b{_READ_EN}\b.{{0,30}}(~?/?\.ssh\b|id_rsa|\.aws/credentials|/etc/shadow)",
        "Reads or moves credentials",
    ),
    _rule(
        "credentials-command",
        BLOCK,
        r"(\bcat\b|\btype\b|\bcopy\b|\bcp\b)\s+[^\n]{0,30}(~/\.ssh|id_rsa|\.aws/credentials|/etc/shadow)|\bprintenv\b|\bos\.environ\b(?!\s*(\[|\.get\b))|\bprocess\.env\b(?!\s*(\.|\[))|\benv\s*\|\s*(curl|nc|base64)\b",
        "Reads credentials, or the whole environment",
    ),
    _rule(
        "credentials-id",
        BLOCK,
        r"\b(kirim|baca|salin|ambil|curi|tampilkan)\b.{0,40}\b(kata sandi|kredensial|kunci api|token)\b",
        "Reads or moves credentials (Indonesian)",
    ),
    _rule(
        "credentials-es",
        BLOCK,
        r"\b(envía|envia|lee|copia|roba|muestra)\b.{0,40}\b(contraseñas?|credenciales|clave de api|tokens?)\b",
        "Reads or moves credentials (Spanish)",
    ),
    _rule(
        "credentials-zh",
        BLOCK,
        r"(发送|读取|复制|窃取|获取|显示).{0,20}(密码|密钥|凭据|令牌)",
        "Reads or moves credentials (Chinese)",
    ),
    _rule(
        "credentials-ar",
        BLOCK,
        r"(أرسل|ارسل|اقرأ|انسخ|اسرق).{0,40}(كلمات? المرور|كلمة السر|بيانات الاعتماد|المفاتيح)",
        "Reads or moves credentials (Arabic)",
    ),
    # Fetching and running remote code
    _rule(
        "remote-pipe",
        BLOCK,
        r"\b(curl|wget|irm|iwr|invoke-webrequest|invoke-restmethod)\b[^\n]{0,120}(\|\s*(sudo\s+)?(sh|bash|zsh|python3?|node|iex|invoke-expression)\b)",
        "Pipes a download into a shell",
    ),
    _rule("remote-iex", BLOCK, r"\biex\s*\(|\binvoke-expression\b", "Runs a downloaded string in PowerShell"),
    _rule(
        "remote-decode",
        BLOCK,
        r"\bbase64\b[^\n]{0,30}(-d|--decode)[^\n]{0,30}\|\s*(sh|bash|python3?)\b",
        "Decodes and runs hidden code",
    ),
    _rule(
        "remote-eval",
        REVIEW,
        r"\b(eval|exec)\s*\(\s*(requests|urllib|urlopen|fetch|atob|base64|bytes\.fromhex)",
        "Evaluates remote or encoded code",
    ),
    _rule(
        "remote-install",
        REVIEW,
        rf"\b(pip3?|npm|pnpm|yarn)\b\s+(install|add)\s+[^\n]{{0,20}}{_URL}",
        "Installs a package from a URL",
    ),
)

_INVISIBLE = {
    **{code: "tag-characters" for code in range(0xE0000, 0xE0080)},
    **{code: "bidi-control" for code in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))},
}
_ZERO_WIDTH = (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF)
_STRIP = {code: None for code in (*_INVISIBLE, *_ZERO_WIDTH)}
_BLOB = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{200,}={0,2}|\b[0-9a-fA-F]{200,}\b")
_DATA_URI = re.compile(r"data:[a-z]+/[a-z0-9.+-]+;base64,", re.IGNORECASE)

EXECUTABLE_SUFFIXES = {
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".psm1", ".bat", ".cmd", ".com", ".exe", ".dll", ".so", ".dylib",
    ".py", ".pyc", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".php", ".lua", ".jar", ".class", ".wasm",
    ".msi", ".scr", ".vbs", ".applescript", ".go", ".rs", ".c", ".cpp",
}  # fmt: skip
EXECUTABLE_DIRS = {"scripts", "bin", "hooks"}
