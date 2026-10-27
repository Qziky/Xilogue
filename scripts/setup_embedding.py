"""Download a verified official embedding model into this repository's ignored data directory."""

import hashlib
import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def download_layer(url, target, size):
    chunk_size = 8 * 1024 * 1024
    pieces = []

    def chunk(index):
        start = index * chunk_size
        end = min(size, start + chunk_size) - 1
        part = target.with_name(target.name + f".part{index:04}")
        if part.is_symlink():
            raise ValueError("unsafe_model_cache")
        if part.is_file() and part.stat().st_size == end - start + 1:
            return part
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
                with urllib.request.urlopen(request, timeout=45) as response:
                    if (
                        response.status != 206
                        or response.headers.get("Content-Range") != f"bytes {start}-{end}/{size}"
                    ):
                        raise ValueError("range_response_rejected")
                    data = response.read(end - start + 2)
                    if len(data) != end - start + 1:
                        raise ValueError("short_model_chunk")
                    part.write_bytes(data)
                    return part
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)

    with ThreadPoolExecutor(max_workers=6) as pool:
        for part in pool.map(chunk, range((size + chunk_size - 1) // chunk_size)):
            pieces.append(part)
    temporary = target.with_suffix(".partial")
    with temporary.open("wb") as destination:
        for part in pieces:
            with part.open("rb") as source:
                while block := source.read(chunk_size):
                    destination.write(block)
    temporary.replace(target)
    for part in pieces:
        part.unlink()


def main():
    root = Path(__file__).resolve().parents[1]
    cache = root / "data" / "embedding-models"
    if not cache.resolve().is_relative_to(root):
        raise ValueError("model_cache_outside_project")
    downloads = cache / "blobs"
    downloads.mkdir(parents=True, exist_ok=True)
    url = "https://registry.ollama.ai/v2/library/embeddinggemma/manifests/latest"
    with urllib.request.urlopen(url, timeout=30) as response:
        raw = response.read(65536)
    manifest = json.loads(raw)
    layers = [manifest["config"], *manifest["layers"]]
    if not any(
        layer.get("digest")
        == "sha256:0800cbac9c2064dde519420e75e512a83cb360de3ad5df176185dc69652fc515"
        for layer in manifest["layers"]
    ):
        raise ValueError("upstream_model_changed_review_required")
    if len(layers) > 16:
        raise ValueError("invalid_model_manifest")
    verified = []
    for layer in layers:
        digest = layer["digest"]
        if (
            not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            or not 0 < layer["size"] < 2_000_000_000
        ):
            raise ValueError("invalid_model_layer")
        target = downloads / digest.replace(":", "-")
        if target.is_symlink():
            raise ValueError("unsafe_model_cache")
        if not target.is_file() or target.stat().st_size != layer["size"]:
            if layer["size"] > 8 * 1024 * 1024:
                download_layer(
                    f"https://registry.ollama.ai/v2/library/embeddinggemma/blobs/{digest}",
                    target,
                    layer["size"],
                )
            else:
                with urllib.request.urlopen(
                    f"https://registry.ollama.ai/v2/library/embeddinggemma/blobs/{digest}",
                    timeout=30,
                ) as response:
                    target.write_bytes(response.read(layer["size"] + 1))
        sha = hashlib.sha256()
        with target.open("rb") as file:
            while block := file.read(8 * 1024 * 1024):
                sha.update(block)
        if target.stat().st_size != layer["size"] or sha.hexdigest() != digest.split(":")[1]:
            raise ValueError("model_layer_hash_mismatch")
        verified.append({"digest": digest, "bytes": layer["size"]})
    destination = (
        cache / "manifests" / "registry.ollama.ai" / "library" / "embeddinggemma" / "latest"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    (cache / "provenance.json").write_text(
        json.dumps(
            {
                "source": url,
                "manifest_sha256": hashlib.sha256(raw).hexdigest(),
                "verified": verified,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "model_directory": str(cache),
                "verified_layers": len(verified),
                "model": "embeddinggemma:latest",
            }
        )
    )


if __name__ == "__main__":
    main()
