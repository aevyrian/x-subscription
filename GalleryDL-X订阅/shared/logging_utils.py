"""Scrub authentication values before writing downloader output to disk."""
import re

SECRET = re.compile(r'''(auth_token|ct0|guest_id\w*|personalization_id|passw\w*|api_?key|secret|token)\s*[:=]\s*("[^"]*"|'[^']*'|\S+)''', re.I)
PROXY = re.compile(r"((?:https?|socks5h?)://)[^\s/@]+(?::[^\s/@]*)?@", re.I)


def redact(value):
    return PROXY.sub(r"\1***@", SECRET.sub(r"\1=***", value or ""))
