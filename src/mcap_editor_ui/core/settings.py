"""Settings: shipped defaults plus the user's overrides, used by the CLI, the terminal wizard and the browser UI.

The defaults live in ``config/defaults.yaml`` inside the package. The user's file
(``$XDG_CONFIG_HOME/mcap_editor_ui/settings.yaml``, usually ``~/.config/mcap_editor_ui/settings.yaml``) holds only values
that differ from the defaults, so a later change of a default reaches every user who did not override it.
``SCHEMA`` describes each setting (section, label, help text, type, choices) for validation and for the settings dialog of the browser UI.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

DEFAULTS_FILE = Path(__file__).parent / "config" / "defaults.yaml"

# key → description; "choices" for fixed values, "type" for free values (with optional min / max)
SCHEMA: dict[str, dict] = {
    "interface.accent": {"section": "Interface", "label": "Accent colour", "type": "choice",
                         "choices": ["orange", "teal", "blue", "violet", "green"],
                         "help": "Colour of the main button, the selection and the focus"},
    "interface.theme": {"section": "Interface", "label": "Theme", "type": "choice", "choices": ["system", "dark", "light"],
                        "help": "System follows the operating system; the moon icon in the top bar switches it too"},
    "sizes.unit": {"section": "Sizes", "label": "Size unit", "type": "choice", "choices": ["smart", "kB", "MB", "GB", "auto"],
                   "help": "One unit per table. Smart: file < 1 GB → kB, < 100 GB → MB, else GB. Auto: each value its own unit"},
    "output.suffix": {"section": "Output", "label": "Name suffix", "type": "str",
                      "help": "Default output name: <input><suffix>.mcap next to the input"},
    "output.keep_latched": {"section": "Output", "label": "Include latched messages", "type": "bool",
                            "help": "Default of the switch: include latched messages (e.g. /tf_static) from before the start"},
    "output.compression": {"section": "Output", "label": "Compression", "type": "choice", "choices": ["keep", "zstd", "lz4", "none"],
                           "help": "Default chunk compression of the output; keep = as the input"},
    "output.split": {"section": "Output", "label": "Split", "type": "choice", "choices": ["off", "duration", "size"],
                     "help": "Default: write one file, or parts of a fixed duration or size"},
    "output.split_duration_s": {"section": "Output", "label": "Part duration", "type": "float", "min": 0.001, "unit": "s",
                                "help": "Length of one part when splitting by duration"},
    "output.split_size_gb": {"section": "Output", "label": "Part size", "type": "float", "min": 0.001, "unit": "GB",
                             "help": "Size of one part when splitting by size (parts end at the first chunk past it)"},
    "output.split_repeat_latched": {"section": "Output", "label": "Latched in every part", "type": "bool",
                                    "help": "When splitting, start every part with the latched messages so it opens correctly on its own; off: only part 0, so joined parts have no duplicates"},
    "editing.history_size": {"section": "Editing", "label": "Undo steps", "type": "int", "min": 1, "max": 500,
                             "help": "How many selection and window changes Ctrl+Z can undo in the browser UI"},
    "ui.port": {"section": "UI", "label": "Port", "type": "int", "min": 1024, "max": 65535,
                 "help": "Port on 127.0.0.1 (the next free one if taken); takes effect at the next start"},
}


def user_file() -> Path:
    """Path of the user's override file."""
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "mcap_editor_ui" / "settings.yaml"


def _flatten(d: dict, prefix: str = "") -> dict:
    """{"a": {"b": 1}} → {"a.b": 1}."""
    out = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def _nest(flat: dict) -> dict:
    """{"a.b": 1} → {"a": {"b": 1}}."""
    out: dict = {}
    for key, v in flat.items():
        *parents, leaf = key.split(".")
        d = out
        for p in parents:
            d = d.setdefault(p, {})
        d[leaf] = v
    return out


def validate(key: str, value):
    """Value of ``key`` converted to its type; ValueError for unknown keys or invalid values."""
    if key not in SCHEMA:
        raise ValueError(f"unknown setting {key}")
    s = SCHEMA[key]
    t = s["type"]
    if t == "choice":
        value = str(value)
        if value not in s["choices"]:
            raise ValueError(f"{key}: {value!r} is not one of {', '.join(s['choices'])}")
        return value
    if t == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key}: expected true or false")
        return value
    if t == "str":
        return str(value)
    value = int(value) if t == "int" else float(value)
    if "min" in s and value < s["min"] or "max" in s and value > s["max"]:
        raise ValueError(f"{key}: {value} outside {s.get('min')} … {s.get('max')}")
    return value


def defaults() -> dict:
    """Shipped default values (flat keys)."""
    return {k: validate(k, v) for k, v in _flatten(yaml.safe_load(DEFAULTS_FILE.read_text())).items()}


def overrides(path: Path | None = None) -> dict:
    """The user's valid overrides (flat keys); invalid or unknown entries are ignored."""
    path = path or user_file()
    if not path.is_file():
        return {}
    out = {}
    for k, v in _flatten(yaml.safe_load(path.read_text()) or {}).items():
        try:
            out[k] = validate(k, v)
        except (ValueError, TypeError):
            continue
    return out


def load(path: Path | None = None) -> dict:
    """Effective settings: defaults updated by the user's overrides (flat keys)."""
    return defaults() | overrides(path)


def get(key: str):
    """One effective setting (reads the files each time, so changes apply without a restart)."""
    return load()[key]


def save(values: dict, path: Path | None = None) -> dict:
    """Store ``values`` (flat keys) as overrides, keeping only those that differ from the defaults; returns them."""
    d = defaults()
    over = {}
    for k, v in values.items():
        v = validate(k, v)
        if v != d[k]:
            over[k] = v
    path = path or user_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if over:
        path.write_text("# mcap_editor_ui user settings – only values that differ from the defaults\n"
                        + yaml.safe_dump(_nest(over), sort_keys=False))
    else:
        path.unlink(missing_ok=True)
    return over
