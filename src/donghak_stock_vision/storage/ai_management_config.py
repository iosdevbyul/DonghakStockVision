"""Private pinned server configuration; never accepted from HTTP clients."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import fields, require
from donghak_stock_vision.models.llm_provider import ModelConfig
from donghak_stock_vision.storage.ai_model_management import ModelManagement


def load_management(
    root: Path, path: Path, pin: str
) -> tuple[ModelManagement, list[dict[str, Any]]]:
    require(
        path.is_absolute()
        and path.resolve() == path
        and path.stat().st_size <= 65536
        and path.stat().st_mode & 0o077 == 0,
        "ai_private_server_config",
    )
    body = path.read_bytes()
    require(hashlib.sha256(body).hexdigest() == pin, "ai_server_config_pin")
    d = fields(json.loads(body), "version default models auth")
    require(
        d["version"] == "ai_management_server_v1"
        and type(d["models"]) is list
        and len(d["models"]) <= 32,
        "ai_server_config_version",
    )
    require(type(d["auth"]) is list and len(d["auth"]) <= 32, "ai_auth_bound")
    entries = []
    for value in d["auth"]:
        v = fields(value, "subject secret_env scopes")
        require(
            type(v["subject"]) is str
            and 0 < len(v["subject"]) <= 128
            and v["scopes"] in (["ai:read"], ["ai:read", "ai:manage"]),
            "ai_auth_configuration",
        )
        require(
            type(v["secret_env"]) is str and v["secret_env"].isidentifier(),
            "ai_auth_secret_reference",
        )
        secret = os.environ.get(v["secret_env"])
        require(secret is not None and len(secret) >= 24, "ai_auth_secret_unavailable")
        entries.append(dict(subject=v["subject"], scopes=v["scopes"], secret=secret))
    return ModelManagement(root, [ModelConfig(**m) for m in d["models"]], d["default"]), entries
