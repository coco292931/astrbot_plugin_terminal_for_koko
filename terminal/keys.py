from __future__ import annotations

import re


SIMPLE_ANSI_KEYS = {
    "enter": "\r",
    "tab": "\t",
    "escape": "\x1b",
    "backspace": "\x7f",
    "delete": "\x1b[3~",
    "insert": "\x1b[2~",
    "home": "\x1b[H",
    "end": "\x1b[F",
    "pageup": "\x1b[5~",
    "pagedown": "\x1b[6~",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
    "space": " ",
}

SIMPLE_TMUX_KEYS = {
    "enter": "Enter",
    "tab": "Tab",
    "escape": "Escape",
    "backspace": "BSpace",
    "delete": "DC",
    "insert": "IC",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "up": "Up",
    "down": "Down",
    "right": "Right",
    "left": "Left",
    "space": "Space",
}

BASE_ALIASES = {
    "esc": "escape",
    "return": "enter",
    "del": "delete",
    "ins": "insert",
    "pgup": "pageup",
    "page_up": "pageup",
    "pgdn": "pagedown",
    "page_down": "pagedown",
    "bspace": "backspace",
    "back_tab": "tab",
    "btab": "tab",
}

MODIFIER_ALIASES = {
    "c": "ctrl",
    "control": "ctrl",
    "ctl": "ctrl",
    "m": "alt",
    "meta": "alt",
    "option": "alt",
    "a": "alt",
    "s": "shift",
}

ARROW_SUFFIX = {"up": "A", "down": "B", "right": "C", "left": "D"}
HOME_END_SUFFIX = {"home": "H", "end": "F"}


def key_to_ansi(key: str) -> str:
    modifiers, base = parse_key(key)
    if not modifiers and base in SIMPLE_ANSI_KEYS:
        return SIMPLE_ANSI_KEYS[base]

    if modifiers == {"shift"} and base == "tab":
        return "\x1b[Z"

    if base in ARROW_SUFFIX or base in HOME_END_SUFFIX:
        suffix = ARROW_SUFFIX.get(base, HOME_END_SUFFIX.get(base, ""))
        return f"\x1b[1;{_xterm_modifier_code(modifiers)}{suffix}"

    if modifiers == {"ctrl"} and len(base) == 1 and base.isalpha():
        return chr(ord(base) - ord("a") + 1)

    if modifiers == {"ctrl"} and base == "space":
        return "\x00"

    if "alt" in modifiers:
        remaining = set(modifiers)
        remaining.remove("alt")
        return "\x1b" + _key_to_ansi_parts(remaining, base)

    if modifiers == {"shift"} and len(base) == 1:
        return base.upper()

    raise ValueError(_unsupported_key_message(key))


def key_to_tmux(key: str) -> str:
    modifiers, base = parse_key(key)
    if not modifiers and base in SIMPLE_TMUX_KEYS:
        return SIMPLE_TMUX_KEYS[base]

    if modifiers == {"shift"} and base == "tab":
        return "BTab"

    if base in SIMPLE_TMUX_KEYS:
        return _tmux_prefix(modifiers) + SIMPLE_TMUX_KEYS[base]

    if re.fullmatch(r"f([1-9]|1[0-2])", base):
        return _tmux_prefix(modifiers) + base.upper()

    if len(base) == 1:
        if modifiers == {"shift"}:
            return base.upper()
        return _tmux_prefix(modifiers) + base

    raise ValueError(_unsupported_key_message(key))


def parse_key(key: str) -> tuple[set[str], str]:
    text = (key or "").strip().lower()
    if not text:
        raise ValueError(_unsupported_key_message(key))

    text = text.replace(" ", "")
    text = _expand_legacy_modifier(text, "ctrl")
    text = _expand_legacy_modifier(text, "control")
    text = _expand_legacy_modifier(text, "alt")
    text = _expand_legacy_modifier(text, "meta")
    text = _expand_legacy_modifier(text, "shift")
    text = text.replace("-", "+")

    parts = [part for part in text.split("+") if part]
    if not parts:
        raise ValueError(_unsupported_key_message(key))

    base = _normalize_base(parts[-1])
    modifiers = {_normalize_modifier(part) for part in parts[:-1]}
    modifiers.discard("")
    if any(part not in {"ctrl", "alt", "shift"} for part in modifiers):
        raise ValueError(_unsupported_key_message(key))
    return modifiers, base


def _key_to_ansi_parts(modifiers: set[str], base: str) -> str:
    if not modifiers and base in SIMPLE_ANSI_KEYS:
        return SIMPLE_ANSI_KEYS[base]
    if modifiers == {"shift"} and base == "tab":
        return "\x1b[Z"
    if modifiers == {"ctrl"} and len(base) == 1 and base.isalpha():
        return chr(ord(base) - ord("a") + 1)
    if modifiers == {"shift"} and len(base) == 1:
        return base.upper()
    if not modifiers and len(base) == 1:
        return base
    raise ValueError(_unsupported_key_message(base))


def _expand_legacy_modifier(text: str, modifier: str) -> str:
    prefix = f"{modifier}_"
    if text.startswith(prefix) and "+" not in text:
        return f"{modifier}+{text[len(prefix):]}"
    return text


def _normalize_base(base: str) -> str:
    base = BASE_ALIASES.get(base, base)
    if re.fullmatch(r"f([1-9]|1[0-2])", base):
        return base
    if len(base) == 1 or base in SIMPLE_ANSI_KEYS:
        return base
    return base.replace("_", "")


def _normalize_modifier(modifier: str) -> str:
    return MODIFIER_ALIASES.get(modifier, modifier)


def _xterm_modifier_code(modifiers: set[str]) -> int:
    code = 1
    if "shift" in modifiers:
        code += 1
    if "alt" in modifiers:
        code += 2
    if "ctrl" in modifiers:
        code += 4
    if code == 1:
        raise ValueError("modified key requires at least one modifier")
    return code


def _tmux_prefix(modifiers: set[str]) -> str:
    parts = []
    if "ctrl" in modifiers:
        parts.append("C")
    if "alt" in modifiers:
        parts.append("M")
    if "shift" in modifiers:
        parts.append("S")
    return "-".join(parts) + "-" if parts else ""


def _unsupported_key_message(key: str) -> str:
    return (
        f"不支持的 key: {key}；可传 enter/tab/escape/backspace/up/down/left/right，"
        "也可传 ctrl+c、ctrl_c、shift+tab、alt+enter、ctrl+shift+left 等组合键"
    )
