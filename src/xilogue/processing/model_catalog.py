"""Read model identifiers without starting services, loading weights or sending evidence."""

import os
import re
from itertools import islice
from pathlib import Path
from urllib.parse import urlsplit

from xilogue.config import Config, ConfigError
from xilogue.processing.ollama import ProviderError
from xilogue.processing.providers import request_json

MAX_MODELS = 2000


def model_ids(values, *, local=False):
    return sorted(
        {
            value
            for value in values
            if isinstance(value, str)
            and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", value)
            and (not local or ":cloud" not in value)
        },
        key=str.casefold,
    )


def cached_models(config):
    root = (
        Path(config.embedding_models_dir).expanduser()
        if config.embedding_models_dir
        else Path.cwd() / "data" / "embedding-models"
    )
    manifests = root / "manifests"
    names = []
    # Match only manifest path names; never read weights or traverse arbitrary depths.
    for path in islice(manifests.glob("*/*/*/*"), MAX_MODELS + 1):
        if not path.is_file() or path.is_symlink():
            continue
        registry, namespace, model, tag = path.relative_to(manifests).parts
        name = ("" if namespace == "library" else namespace + "/") + model + ":" + tag
        if registry != "registry.ollama.ai":
            name = registry + "/" + name
        names.append(name)
    return {
        "models": model_ids(names[:MAX_MODELS], local=True),
        "source": "project_cache",
        "truncated": len(names) > MAX_MODELS,
    }


def list_models(current, credentials, data):
    allowed = {
        "role",
        "provider",
        "base_url",
        "api_key_env",
        "api_key",
        "embedding_port",
        "embedding_models_dir",
    }
    if (
        not isinstance(data, dict)
        or set(data) - allowed
        or not {"role", "provider"} <= set(data)
        or data["role"] not in ("analysis", "embedding")
    ):
        raise ConfigError("无效的模型列表请求")
    role = data["role"]
    prefix = "ai" if role == "analysis" else "embedding"
    changes = {
        prefix + "_provider": data["provider"],
        prefix + "_model": "catalog-placeholder",
        prefix + "_base_url": data.get("base_url", ""),
        prefix + "_api_key_env": data.get("api_key_env", ""),
    }
    if role == "embedding":
        for field in ("embedding_port", "embedding_models_dir"):
            if field in data:
                changes[field] = data[field]
    config = Config.from_dict(current.to_dict() | changes)
    timeout = min(15, config.ai_timeout_seconds)
    if data["provider"] == "ollama":
        if "api_key" in data:
            raise ConfigError("Ollama 本机目录不使用 API Key")
        if role == "embedding" and config.embedding_port == 0:
            return cached_models(config)
        port = config.embedding_port if role == "embedding" else 11434
        payload = request_json(f"http://127.0.0.1:{port}", "", timeout, "/api/tags", method="GET")
        items = payload.get("models")
        if not isinstance(items, list):
            raise ProviderError("api_response_invalid")
        names = [
            item.get("name")
            for item in items[:MAX_MODELS]
            if isinstance(item, dict)
            and not item.get("remote_host")
            and not item.get("remote_model")
        ]
        return {
            "models": model_ids(names, local=True),
            "source": "service",
            "truncated": len(items) > MAX_MODELS,
        }
    base = getattr(config, prefix + "_base_url")
    if "api_key" in data:
        # Draft credentials are used once, never persisted or returned.
        key = data["api_key"]
        if key is None:
            key = os.environ.get(getattr(config, prefix + "_api_key_env"), "")
    else:
        key = credentials.get(config, role)
    if (
        not isinstance(key, str)
        or len(key) > 4096
        or any(ord(char) < 33 or ord(char) > 126 for char in key)
    ):
        raise ConfigError("API Key 格式无效")
    if not key and urlsplit(base).hostname not in ("127.0.0.1", "::1", "localhost"):
        raise ProviderError("api_key_missing")
    payload = request_json(base, key, timeout, "/models", method="GET")
    items = payload.get("data")
    if not isinstance(items, list):
        raise ProviderError("api_response_invalid")
    return {
        "models": model_ids(
            [item.get("id") for item in items[:MAX_MODELS] if isinstance(item, dict)]
        ),
        "source": "service",
        "truncated": len(items) > MAX_MODELS or bool(payload.get("has_more")),
    }
