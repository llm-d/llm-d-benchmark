#!/usr/bin/env python3
"""Download kellyhao/mmshopbench (~42 MB subset) and convert to AIPerf multi_turn JSONL.

This script:
1. Downloads `multi_run_hardcase_merged_num292_regen.json` (1.77 MB) from HuggingFace.
2. Downloads all 400 referenced `val_pic/*.jpg` files (~40 MB) concurrently, skipping
   `bm25.pkl` (87.6 MB) and `image_embeddings.npy` (424.1 MB).
3. Transcodes any WebP-encoded `val_pic/*.jpg` files to true RGB JPEG (preserving exact
   width x height pixel dimensions) so NVIDIA AIPerf's PIL format check (`JPEG`/`PNG`) passes.
4. Converts the 288 multi-turn shopping conversations into `aiperf_agentic_multimodal.jsonl`
   for `aiperf profile --custom-dataset-type multi_turn`.
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HF_RESOLVE_BASE = "https://huggingface.co/datasets/kellyhao/mmshopbench/resolve/main"
TRACE_FILENAME = "multi_run_hardcase_merged_num292_regen.json"

SHOPPING_AGENT_SYSTEM_PROMPT = (
    "You are an expert multimodal e-commerce shopping assistant. Analyze user product "
    "photos and attributes across turns, ask clarifying questions when specifications "
    'are ambiguous, and emit <Card>{"call_name": "ItemListCard", ...}</Card> '
    "recommendations with matching items and price breakdowns."
)


def safe_resolve_under(base_dir: Path, rel_path: str) -> Path:
    """Resolve `rel_path` strictly inside `base_dir` to prevent path traversal."""
    base_resolved = base_dir.resolve()
    candidate = (base_resolved / rel_path).resolve()
    if not candidate.is_relative_to(base_resolved):
        raise ValueError(f"Unsafe relative path outside target directory: {rel_path}")
    return candidate


def ensure_true_jpeg(img_path: Path) -> bool:
    """Ensure `img_path` is encoded as a true RGB JPEG (transcoding WebP/RGBA if needed)."""
    with img_path.open("rb") as f:
        header = f.read(16)
    # Standard JPEG starts with SOI marker 0xFFD8; skip re-encoding if already JPEG
    if header.startswith(b"\xff\xd8\xff"):
        return False

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            f"{img_path} is not a native JPEG (header={header[:12]!r}) and Pillow is not "
            "installed to transcode it. Run inside the llm-d-benchmark container or "
            "install Pillow (`pip install Pillow`)."
        ) from exc

    with Image.open(img_path) as im:
        rgb = im.convert("RGB")
        tmp_path = img_path.with_suffix(img_path.suffix + ".jpeg.tmp")
        rgb.save(tmp_path, format="JPEG", quality=95)
    tmp_path.replace(img_path)
    return True


def download_file(rel_path: str, dest_path: Path, retries: int = 3) -> Path:
    """Download a single file from HuggingFace if not already present."""
    if not (dest_path.exists() and dest_path.stat().st_size > 0):
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        quoted_rel = "/".join(
            urllib.parse.quote(part, safe="") for part in rel_path.split("/")
        )
        url = f"{HF_RESOLVE_BASE}/{quoted_rel}"
        if not url.startswith("https://"):
            raise ValueError(f"Only HTTPS URLs are permitted: {url}")

        last_err: Exception | None = None
        for _ in range(retries):
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "llm-d-benchmark/1.0"}
                )
                with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                    data = resp.read()
                tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
                tmp_path.write_bytes(data)
                tmp_path.replace(dest_path)
                break
            except Exception as exc:
                last_err = exc
        else:
            raise RuntimeError(f"Failed to download {url}: {last_err}") from last_err

    if dest_path.suffix.lower() in {".jpg", ".jpeg"}:
        ensure_true_jpeg(dest_path)
    return dest_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download kellyhao/mmshopbench subset and convert to AIPerf multi_turn JSONL."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/requests/agentic_multimodal"),
        help="Directory to store downloaded subset and converted JSONL (default: /requests/agentic_multimodal)",
    )
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=None,
        help="Optional runtime base directory written into JSONL image paths (if different from --output-dir)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=16,
        help="Concurrent download workers for val_pic/*.jpg (default: 16)",
    )
    args = parser.parse_args()

    out_dir = args.output_dir.resolve()
    runtime_base = (args.runtime_dir or out_dir) / "raw"
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    # 1. Download conversation trace JSON (1.77 MB)
    trace_path = safe_resolve_under(raw_dir, TRACE_FILENAME)
    print(f"[1/3] Downloading {TRACE_FILENAME} -> {trace_path} ...")
    download_file(TRACE_FILENAME, trace_path)

    conversations = json.loads(trace_path.read_text(encoding="utf-8"))

    # 2. Download all 400 unique val_pic/*.jpg images and ensure RGB JPEG format
    unique_images: dict[str, Path] = {}
    for conv in conversations:
        for r in conv.get("round_results", []):
            for item in r.get("model_input", []):
                if item.get("type") == "image" and item.get("value"):
                    rel_img = str(item["value"])
                    unique_images[rel_img] = safe_resolve_under(raw_dir, rel_img)

    print(
        f"[2/3] Downloading & verifying {len(unique_images)} images in val_pic/ "
        f"with {args.workers} workers ..."
    )
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(download_file, rel_img, dest): rel_img
            for rel_img, dest in unique_images.items()
        }
        for fut in as_completed(futures):
            fut.result()
            completed += 1
            if completed % 50 == 0 or completed == len(unique_images):
                print(f"      Processed {completed}/{len(unique_images)} images")

    # 3. Convert conversations to AIPerf multi_turn JSONL
    out_jsonl = out_dir / "aiperf_agentic_multimodal.jsonl"
    print(f"[3/3] Converting {len(conversations)} conversations -> {out_jsonl} ...")
    written = 0
    total_turns = 0
    total_image_turns = 0

    with out_jsonl.open("w", encoding="utf-8") as out:
        for conv in conversations:
            turns = []
            for idx, r in enumerate(conv.get("round_results", [])):
                imgs = [
                    str(safe_resolve_under(runtime_base, str(item["value"])))
                    for item in r.get("model_input", [])
                    if item.get("type") == "image" and item.get("value")
                ]
                txts = [
                    str(item["text"])
                    for item in r.get("model_input", [])
                    if item.get("type") == "text" and item.get("text")
                ]
                user_text = (
                    " ".join(txts).strip()
                    or "Please identify the product in this image and recommend matching items currently available for sale."
                )
                if idx == 0:
                    user_text = f"{SHOPPING_AGENT_SYSTEM_PROMPT}\n\nUser: {user_text}"

                ref_chars = len(r.get("model_output", ""))
                target_out_tokens = max(64, min(512, ref_chars // 2))

                turn_entry: dict[str, object] = {
                    "texts": [user_text],
                    "output_length": target_out_tokens,
                }
                if imgs:
                    turn_entry["images"] = imgs
                    total_image_turns += 1
                turns.append(turn_entry)
                total_turns += 1

            if turns:
                record = {
                    "session_id": f"mmshop_{conv['conversation_id']}",
                    "turns": turns,
                }
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1

    print(
        f"Done! Wrote {written} sessions ({total_turns} total turns, "
        f"{total_image_turns} image turns) to {out_jsonl}"
    )


if __name__ == "__main__":
    main()
