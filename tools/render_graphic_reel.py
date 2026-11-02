#!/usr/bin/env python3
"""Rebuild original graphic-only career Reels as QA artifacts, never auto-publish."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 25

def font_path():
    p = os.environ.get("REEL_FONT")
    candidates = [p] if p else [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    raise RuntimeError("Noto Sans CJK JP required (fonts-noto-cjk)")

def fitted(draw, text, width, size, font, minimum=24):
    while size >= minimum:
        f = ImageFont.truetype(font, size)
        if draw.textbbox((0, 0), text, font=f)[2] <= width:
            return f
        size -= 2
    raise ValueError("unreadable text: " + text[:30])

def validate(r):
    # Source recovery changes only the QA-artifact state, not permission to publish.
    # Accept it for rendering while the separate immutable-hosting and queue gates remain closed.
    if r.get("status") not in (
        "rendered_locally_pending_immutable_hosting_and_queue",
        "reviewed_for_render",
        "source_restored_qa_artifact_only_not_hosted_or_queued",
    ):
        raise ValueError("recipe not reviewed for QA render")
    if r.get("source_type") != "original_graphic_cards" or r.get("audio") != "none" or r.get("third_party_assets") != []:
        raise ValueError("graphic-only, silent, original content required")
    scenes = r.get("scenes", [])
    if not r.get("draft_id", "").startswith("R-") or not 1 <= len(scenes) <= 8:
        raise ValueError("invalid draft or scenes")
    duration = sum(float(s["seconds"]) for s in scenes)
    if not 15 <= duration <= 25 or abs(duration - float(r["duration_seconds"])) > 0.02:
        raise ValueError("duration mismatch")
    caption = r.get("caption", "")
    if not all(t in caption for t in ("DM", "本人同意", "ADVANCE")) or "DM" not in scenes[-1]["heading"]:
        raise ValueError("DM and disclosure required")
    for scene in scenes:
        if any(not isinstance(scene.get(k), str) or not scene[k].strip() for k in ("eyebrow", "heading", "note")):
            raise ValueError("missing scene copy")
    return duration

def frame(scene, index, count, font):
    im = Image.new("RGB", (W, H), "#071323")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((70, 145, 1010, 1775), radius=48, fill="#102a3a", outline="#42627a", width=3)
    d.rounded_rectangle((100, 185, 570, 256), radius=28, fill="#f2b75f")
    d.text((125, 199), "経験を言葉にする", font=fitted(d, "経験を言葉にする", 420, 37, font), fill="#071323")
    d.text((110, 355), scene["eyebrow"], font=fitted(d, scene["eyebrow"], 860, 58, font), fill="#f2b75f")
    d.text((110, 570), scene["heading"], font=fitted(d, scene["heading"], 860, 81, font, 48), fill="#f6f9fb")
    d.rounded_rectangle((105, 825, 975, 1120), radius=32, fill="#1c3a50")
    d.text((140, 890), scene["note"], font=fitted(d, scene["note"], 790, 43, font), fill="#f6f9fb")
    if index == count - 1:
        for n, line in enumerate((
            "運営：吉本｜ADVANCE採用・PBC集客支援に関与",
            "紹介は関係性の説明と本人同意後に行います")):
            d.text((112, 1325 + n * 63), line, font=fitted(d, line, 850, 30, font), fill="#d8e5ed")
    else:
        line = "次の仕事の選択肢を、焦らず整理する。"
        d.text((110, 1390), line, font=fitted(d, line, 850, 32, font), fill="#d8e5ed")
    d.text((110, 1630), "@shigoto_sentakushi", font=ImageFont.truetype(font, 32), fill="#f2b75f")
    for j in range(count):
        d.rounded_rectangle((110 + j * 170, 1690, 255 + j * 170, 1700), radius=5,
                            fill="#f2b75f" if j <= index else "#416078")
    return im

def run(recipe_path, output_path):
    recipe = json.loads(Path(recipe_path).read_text(encoding="utf-8"))
    expected = validate(recipe)
    font = font_path()
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="advance-reel-") as tmp:
        lines = []
        for i, scene in enumerate(recipe["scenes"]):
            img = Path(tmp) / f"frame_{i}.png"
            frame(scene, i, len(recipe["scenes"]), font).save(img)
            clip = Path(tmp) / f"clip_{i}.mp4"
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-i", str(img),
                            "-frames:v", str(round(float(scene["seconds"]) * FPS)), "-r", str(FPS),
                            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                            "-pix_fmt", "yuv420p", str(clip)], check=True)
            lines.append(f"file '{clip}'\n")
        manifest = Path(tmp) / "concat.txt"
        manifest.write_text("".join(lines))
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(manifest), "-c", "copy", "-movflags", "+faststart", str(output)], check=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration:stream=codec_name,width,height,pix_fmt", "-of", "json", str(output)],
                           check=True, capture_output=True, text=True)
    info = json.loads(probe.stdout)
    video = next(s for s in info["streams"] if s.get("codec_name") == "h264")
    duration = float(info["format"]["duration"])
    if video["width"] != W or video["height"] != H or video.get("pix_fmt") != "yuv420p" or abs(duration - expected) > 0.1:
        raise RuntimeError("media QA failed")
    qa = {"draft_id": recipe["draft_id"], "video_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
          "bytes": output.stat().st_size, "duration_seconds": duration, "width": W, "height": H,
          "audio": "none", "status": "qa_artifact_only_not_hosted_or_queued"}
    (output.parent / (output.stem + ".qa.json")).write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(qa, ensure_ascii=False))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args.recipe, args.output)
