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
# Recorded and shown, and never changes a verdict.
NOTE = "note"


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    pattern: re.Pattern[str]
    message: str
    # A match doesn't count when the words just before it match this, such as "never" before "include".
    unless_before: re.Pattern[str] | None = None


def _rule(rule_id: str, severity: str, pattern: str, message: str, unless_before: str | None = None) -> Rule:
    unless = re.compile(unless_before, re.IGNORECASE) if unless_before else None
    return Rule(rule_id, severity, re.compile(pattern, re.IGNORECASE), message, unless)


_URL = r"https?://"
_SECRET_EN = (
    r"(\.ssh\b|id_rsa|\.aws/credentials|\.env\b|credentials?|api[_ -]?keys?|secret|passwords?|access[_ -]?tokens?)"
)
_READ_EN = r"(read|cat|send|copy|upload|dump|exfiltrate|steal|leak|collect)"
_WHOSE = r"(the user'?s?|users?'|their|all|every|any|stored|saved|local|your)"
# Where secrets are kept: key files, cloud and cluster config, and env files. Not .env.example and the like.
_SECRET_PLACE = (
    r"(~/\.aws\b|\.aws/(credentials|config)\b|~/\.ssh\b|\.ssh/|\bid_(rsa|ed25519|ecdsa|dsa)\b|(?<![\w.])\.env\b(?!\.)"
    r"|\.netrc\b|\.git-credentials\b|\.kube/config\b|\.docker/config\.json\b|/etc/shadow\b"
    r"|application_default_credentials)"
)
# A secret as data: a secret place, an environment variable named like a secret, or the whole environment.
SECRET_SOURCE = re.compile(
    _SECRET_PLACE + r"|\$\{?[a-z_]*(secret|token|passw(or)?d|api_?key|private_?key|credential)[a-z_]*\}?"
    r"|\bprintenv\b|\benv\s*\|",
    re.IGNORECASE,
)
# Words that turn an instruction into a warning against it.
_NEGATED = r"\b(never|not|don't|dont|avoid|without|no)\b[^.!?]{0,20}$"
# Gaps that stay inside one sentence.
_GAP = "[^.!?]"

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
        "conceal-secret",
        BLOCK,
        rf"{_SECRET_PLACE}{_GAP}{{0,120}}\b(keep|leave|omit|exclude|hide)\b{_GAP}{{0,40}}\b(out of|from)\b{_GAP}{{0,20}}"
        r"\b(summary|summaries|reply|answer|response|report|message|notes|them|the user|the person)\b",
        "Keeps a secret it touched out of what the person is told",
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
        # A file, a variable, or a command's output sent as data. A body written out in full is an ordinary request.
        # It blocks only when what's sent is a secret: see _severity. Otherwise it's for a person to look at.
        r"\b(curl|wget|invoke-webrequest|invoke-restmethod|iwr|irm)\b[^\n]*?\s("
        r"(--upload-file|-t|--post-file|-infile)(\s+|=)\S"
        r"|(-d|--data(-binary|-raw|-urlencode|-ascii)?|-f|--form|--post-data|-body)(\s+|=)['\"]?([^'\"\s=]*=)?['\"]?"
        r"(@|\$|`|<\())",
        "Uploads data with a command",
    ),
    _rule(
        "exfil-reply",
        BLOCK,
        rf"\b(include|put|paste|add|insert|embed|append|copy|attach|quote|echo|print|dump)\b{_GAP}{{0,60}}"
        rf"{_SECRET_PLACE}{_GAP}{{0,60}}\b(in|into|to|inside|within)\b{_GAP}{{0,15}}"
        r"((your|the|each|every)\s+(reply|answer|response)|\b(replies|answers|chat|conversation)\b)",
        "Puts a secret into the agent's own reply",
        unless_before=_NEGATED,
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
        # Into something that runs what it reads. python -m json.tool, jq, and other formatters only print it.
        r"\b(curl|wget|irm|iwr|invoke-webrequest|invoke-restmethod)\b[^\n]{0,120}\|\s*(sudo\s+)?"
        r"((sh|bash|zsh|dash|ksh|fish|iex|invoke-expression|perl|ruby|php)\b|python[0-9.]*\b(?!\s+-m\b)|node\b)",
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
# Behaviour rules that are also checked across a few lines, because rewording over a line break is an easy dodge.
WINDOW_RULES = ("override-", "conceal-", "exfil-", "credentials-")
WINDOW_LINES = 3
# In code, only an instruction hidden in comments is checked across lines. Code itself spans lines all the time.
CODE_WINDOW_RULES = ("override-", "conceal-")
# Rules about one shell command. In code they read a command continued over lines as one, and nothing else.
COMMAND_RULES = ("exfil-curl", "remote-pipe", "credentials-command")
_CONTINUED = ("\\", "`", "^")
# Shortest encoded runs worth decoding. Shorter ones are mostly ids and hashes.
MIN_BASE64 = 24
MIN_HEX = 32
_BASE64_RUN = re.compile(rf"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{{{MIN_BASE64},}}={{0,2}}")
_HEX_RUN = re.compile(rf"(?<![0-9a-fA-F])(?:[0-9a-fA-F]{{2}}){{{MIN_HEX // 2},}}(?![0-9a-fA-F])")
# Latin look-alikes from Cyrillic and Greek. They're folded only inside a word that mixes alphabets, so real Russian
# or Greek text is left alone.
CONFUSABLES = str.maketrans(
    "аеорсухіјѕԁԛԝаАВЕКМНОРСТХІЈЅοαεικνρτυχΑΒΕΖΗΙΚΜΝΟΡΤΥΧ",
    "aeopcyxijsdqwaABEKMHOPCTXIJSoaeiknptuxABEZHIKMNOPTYX",
)
_LATIN = re.compile(r"[a-zA-Z]")
_OTHER_SCRIPT = re.compile(r"[\u0370-\u03ff\u0400-\u04ff]")
# Letters that pass for Latin ones. Others, like the Greek Δ, μ, or π of maths and units, don't hide a word.
LOOKALIKES = frozenset(chr(code) for code in CONFUSABLES)
_BLOB = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{200,}={0,2}|\b[0-9a-fA-F]{200,}\b")
_DATA_URI = re.compile(r"data:[a-z]+/[a-z0-9.+-]+;base64,", re.IGNORECASE)

EXECUTABLE_SUFFIXES = {
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".psm1", ".bat", ".cmd", ".com", ".exe", ".dll", ".so", ".dylib",
    ".py", ".pyc", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".php", ".lua", ".jar", ".class", ".wasm",
    ".msi", ".scr", ".vbs", ".applescript", ".go", ".rs", ".c", ".cpp",
}  # fmt: skip
EXECUTABLE_DIRS = {"scripts", "bin", "hooks"}
