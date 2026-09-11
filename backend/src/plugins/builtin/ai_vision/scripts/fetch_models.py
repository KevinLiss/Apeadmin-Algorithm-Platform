"""内置模型一键获取脚本：下载 + SHA256 校验 + 断点续传提示。

用途：模型 ONNX 文件不入 git 仓库（体积原因），新用户克隆仓库后运行本脚本
即可补齐内置模型，全程自动校验 SHA256，无需手工核对。

用法（在 backend 目录）::

    python -m src.plugins.builtin.ai_vision.scripts.fetch_models

来源策略（按优先级）:
1. manifest.json 中每个模型条目的 ``url`` 字段（推荐直链）
2. ``--mirror`` 指定的镜像地址前缀（如 hf-mirror.com 等加速环境）
3. 手动下载：脚本会打印各模型的手动下载地址与目标路径，放置后重跑校验通过

说明:
- 文件已存在且校验通过时自动跳过（幂等，可重复运行）
- 校验不匹配时重新下载（视为下载不完整或版本不符）
- 网络受限环境（无法访问 GitHub）请先配置代理或使用 --mirror
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parents[1] / "assets" / "models"
MANIFEST_PATH = MODELS_DIR / "manifest.json"

CHUNK = 1 << 20  # 1MB


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        raise SystemExit(f"manifest.json 不存在: {MANIFEST_PATH}")
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f).get("models", [])


def download(url: str, dest: Path, timeout: int = 300) -> None:
    """流式下载到目标文件。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "apeadmin-fetch-models/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
        total = resp.getheader("Content-Length")
        total = int(total) if total else None
        done = 0
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                sys.stdout.write(f"\r  下载中 {pct:3d}% ({done}/{total} bytes)")
                sys.stdout.flush()
    sys.stdout.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="下载并校验 ai_vision 内置模型")
    parser.add_argument(
        "--mirror",
        default="",
        help="镜像地址前缀，替换直链 host 部分（如无法直连 GitHub 时使用）",
    )
    args = parser.parse_args()

    models = load_manifest()
    if not models:
        raise SystemExit("manifest.json 中没有模型条目")

    failed: list[str] = []
    for item in models:
        name = item["name"]
        dest = MODELS_DIR / item["file"]
        expect = str(item["sha256"]).lower()

        if dest.exists() and sha256_of(dest) == expect:
            print(f"[OK] {name} 已存在且校验通过，跳过")
            continue

        url = item.get("url", "")
        if args.mirror and url.startswith("https://github.com/"):
            url = args.mirror.rstrip("/") + "/https://github.com/" + url[len("https://github.com/"):]

        if url:
            try:
                print(f"[下载] {name} <- {url}")
                download(url, dest)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                failed.append(name)
                print(f"[失败] {name}: {exc}")
                continue
        else:
            failed.append(name)
            print(f"[缺失] {name} 无下载地址，请手动下载")
            print(f"       来源: {item.get('manual_url') or item.get('source', '见 README')}")
            print(f"       放置到: {dest}")
            continue

        actual = sha256_of(dest)
        if actual == expect:
            print(f"[OK] {name} 下载完成，SHA256 校验通过")
        else:
            failed.append(name)
            print(f"[失败] {name} SHA256 不匹配（期望 {expect[:12]}… 实际 {actual[:12]}…），请重试或手动下载")

    if failed:
        print(f"\n{len(failed)} 个模型未就绪: {', '.join(failed)}")
        print("手动下载放置到 assets/models/ 后重跑本脚本可自动校验。")
        sys.exit(1)
    print("\n全部内置模型就绪。")


if __name__ == "__main__":
    main()
