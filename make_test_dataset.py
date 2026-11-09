"""Build the evaluation dataset: original trip, other trip, and transformed candidates.

Usage:
    python make_test_dataset.py                      # synthetic original + other trip
    python make_test_dataset.py --original my.mp4 --other other.mp4   # real footage

Writes dataset/*.mp4, dataset/evaluation_manifest.csv and dataset/transform_log.json
(exact ffmpeg commands / parameters). FFmpeg comes from the imageio-ffmpeg package
(https://github.com/imageio/imageio-ffmpeg); OpenCV and NumPy are used for frame edits.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
WIDTH, HEIGHT, FPS = 640, 360, 30
LOG: list[dict[str, str]] = []


def synthetic_trip(path: Path, seed: int, seconds: int) -> None:
    """Procedural dashcam-like footage: scrolling buildings, road, lane marks, cars."""
    rng = np.random.default_rng(seed)
    frames = seconds * FPS
    speed = 4
    pano_w = WIDTH + frames * speed
    pano = np.zeros((HEIGHT, pano_w, 3), np.uint8)
    sky = rng.integers(120, 230, 3)
    for y in range(HEIGHT // 2):
        pano[y] = (sky * (0.7 + 0.3 * y / (HEIGHT / 2))).astype(np.uint8)
    x = 0
    while x < pano_w:
        w = int(rng.integers(60, 220))
        h = int(rng.integers(60, 190))
        color = tuple(int(c) for c in rng.integers(30, 220, 3))
        top = HEIGHT // 2 - h
        cv2.rectangle(pano, (x, top), (x + w, HEIGHT // 2), color, -1)
        for wy in range(top + 8, HEIGHT // 2 - 8, 22):
            for wx in range(x + 8, x + w - 14, 20):
                if rng.random() < 0.6:
                    shade = int(rng.integers(150, 255))
                    cv2.rectangle(pano, (wx, wy), (wx + 10, wy + 12), (shade, shade, shade - 30), -1)
        x += w + int(rng.integers(0, 40))
    pano[HEIGHT // 2:] = (70, 70, 72)
    for x in range(0, pano_w, 90):
        cv2.rectangle(pano, (x, HEIGHT * 3 // 4), (x + 45, HEIGHT * 3 // 4 + 6), (230, 230, 230), -1)
    for _ in range(pano_w // 160):
        cx = int(rng.integers(0, pano_w))
        cy = int(rng.integers(HEIGHT // 2 + 20, HEIGHT - 40))
        color = tuple(int(c) for c in rng.integers(0, 255, 3))
        cv2.rectangle(pano, (cx, cy), (cx + 70, cy + 32), color, -1)

    tmp = path.with_suffix(".raw.mp4")
    writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (WIDTH, HEIGHT))
    for index in range(frames):
        offset = index * speed
        frame = pano[:, offset:offset + WIDTH].copy()
        bounce = int(4 * np.sin(index / 7.0))
        frame = np.roll(frame, bounce, axis=0)
        noise = rng.normal(0, 3, frame.shape)
        frame = np.clip(frame.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        cv2.putText(frame, "", (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        writer.write(frame)
    writer.release()
    run(["-i", str(tmp), "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-an", str(path)], path.name, "synthetic trip")
    tmp.unlink()


def run(args: list[str], output_name: str, note: str) -> None:
    command = [FFMPEG, "-y", "-loglevel", "error", *args]
    subprocess.run(command, check=True)
    LOG.append({"output": output_name, "note": note, "command": " ".join(command)})


def read_frames(path: Path) -> list[np.ndarray]:
    capture = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frames.append(frame)
    capture.release()
    return frames


def write_frames(frames: list[np.ndarray], path: Path, note: str) -> None:
    height, width = frames[0].shape[:2]
    tmp = path.with_suffix(".raw.mp4")
    writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (width, height))
    for frame in frames:
        writer.write(frame)
    writer.release()
    run(["-i", str(tmp), "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-an", str(path)], path.name, note)
    tmp.unlink()


def normalise(source: Path, target: Path) -> None:
    run(["-i", str(source), "-vf", f"scale={WIDTH}:{HEIGHT},fps={FPS}", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-an", str(target)], target.name, "normalise real footage")


def build(folder: Path, original: Path) -> list[tuple[str, str, str, str]]:
    other = folder / "other_trip.mp4"
    frames = read_frames(original)
    other_frames = read_frames(other)
    total = len(frames)
    cases: list[tuple[str, str, str, str]] = []

    def ff(name: str, args: list[str], note: str, case: str, scenario: str, expected: str) -> None:
        run(["-i", str(original), *args, "-an", str(folder / name)], name, note)
        cases.append((case, name, scenario, expected))

    def py(name: str, edited: list[np.ndarray], note: str, case: str, scenario: str, expected: str) -> None:
        write_frames(edited, folder / name, note)
        cases.append((case, name, scenario, expected))

    cases.append(("AUTH-001", "original_trip.mp4", "authentic_exact", "true"))
    ff("trim_start.mp4", ["-ss", "5", "-c:v", "libx264"], "drop first 5 s", "TEMP-001", "trimmed_beginning", "true")
    ff("trim_end.mp4", ["-t", str(total / FPS - 5), "-c:v", "libx264"], "drop last 5 s", "TEMP-002", "trimmed_end", "true")
    ff("trip_segment.mp4", ["-ss", "10", "-t", "15", "-c:v", "libx264"], "15 s segment starting at 10 s", "TEMP-003", "extracted_segment", "true")
    py("shifted_5s.mp4", other_frames[:5 * FPS] + frames, "5 s of other footage prepended (time shift +5 s)", "TEMP-004", "time_shift_plus_5_seconds", "true")
    ff("speed_0_9.mp4", ["-vf", "setpts=PTS/0.9"], "playback 0.9x", "TEMP-005", "playback_speed_0_9x", "true")
    ff("speed_1_1.mp4", ["-vf", "setpts=PTS/1.1"], "playback 1.1x", "TEMP-006", "playback_speed_1_1x", "true")
    ff("fps_15.mp4", ["-vf", "fps=15"], "15 fps", "TEMP-007", "frame_rate_15_fps", "true")
    ff("fps_60.mp4", ["-vf", "fps=60"], "60 fps", "TEMP-008", "frame_rate_60_fps", "true")
    py("missing_frames.mp4", [f for i, f in enumerate(frames) if i % 10 != 9], "every 10th frame deleted", "TEMP-009", "missing_frames", "true")
    duplicated = [f for i, f in enumerate(frames) for _ in range(2 if i % 10 == 0 else 1)]
    py("duplicated_frames.mp4", duplicated, "every 10th frame duplicated", "TEMP-010", "duplicated_frames", "true")
    block = 15
    reordered = []
    for start in range(0, total, block * 2):
        a, b = frames[start:start + block], frames[start + block:start + block * 2]
        reordered += b + a
    py("reordered_frames.mp4", reordered, "swap adjacent 0.5 s blocks", "TEMP-011", "reordered_frames", "false")

    ff("h265_reencoded.mp4", ["-c:v", "libx265", "-crf", "28"], "H.265 crf 28", "TRANS-001", "reencode_h264_to_h265", "true")
    ff("bitrate_changed.mp4", ["-b:v", "300k"], "300 kbit/s", "TRANS-002", "bitrate_changed", "true")
    ff("resolution_changed.mp4", ["-vf", "scale=320:-2"], "320 px wide", "TRANS-003", "resolution_changed", "true")
    ff("quality_changed.mp4", ["-c:v", "libx264", "-crf", "38"], "crf 38 (strong compression)", "TRANS-004", "compression_quality_changed", "true")
    ff("brightness_changed.mp4", ["-vf", "eq=brightness=0.08"], "brightness +0.08", "TRANS-005", "brightness_changed", "true")
    ff("contrast_changed.mp4", ["-vf", "eq=contrast=1.25"], "contrast 1.25", "TRANS-006", "contrast_changed", "true")
    rng = np.random.default_rng(1)
    noisy = []
    for frame in frames:
        out = frame.copy()
        mask = rng.random(out.shape[:2])
        out[mask < 0.01] = 0
        out[mask > 0.99] = 255
        noisy.append(out)
    py("salt_pepper.mp4", noisy, "salt-and-pepper 2% of pixels", "TRANS-007", "salt_and_pepper_noise", "true")
    ff("moderate_noise.mp4", ["-vf", "noise=alls=12:allf=t+u"], "gaussian-like noise alls=12", "TRANS-008", "moderate_image_noise", "true")
    marked = []
    for frame in frames:
        out = frame.copy()
        overlay = out.copy()
        cv2.rectangle(overlay, (420, 20), (620, 90), (0, 0, 0), -1)
        out = cv2.addWeighted(overlay, 0.7, out, 0.3, 0)
        cv2.putText(out, "LOGO", (450, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
        marked.append(out)
    py("watermark.mp4", marked, "logo box 200x70 px (about 6% of image)", "TRANS-009", "watermark_overlay", "true")
    ff("cropped.mp4", ["-vf", "crop=iw*0.85:ih*0.85"], "centre crop 85%", "TRANS-010", "cropped_image", "true")

    cases.append(("AUTH-002", "other_trip.mp4", "video_from_another_trip", "false"))
    partial = frames[:]
    start, end = 15 * FPS, 25 * FPS
    partial[start:end] = other_frames[start:end]
    py("partially_replaced.mp4", partial, "10 s (15-25 s) replaced by other trip", "AUTH-003", "partially_modified_section", "true")
    half = len(other_frames) // 2
    py("other_trip_rotated.mp4", other_frames[half:] + other_frames[:half], "other trip, halves swapped (second non-matching trip)", "AUTH-004", "video_from_another_trip_2", "false")
    py("reversed_original.mp4", frames[::-1], "original played backwards", "AUTH-005", "reversed_playback", "false")
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=Path("dataset"))
    parser.add_argument("--original", type=Path)
    parser.add_argument("--other", type=Path)
    parser.add_argument("--seconds", type=int, default=40)
    args = parser.parse_args()
    folder = args.folder
    folder.mkdir(parents=True, exist_ok=True)
    if args.original and args.other:
        normalise(args.original, folder / "original_trip.mp4")
        normalise(args.other, folder / "other_trip.mp4")
    else:
        synthetic_trip(folder / "original_trip.mp4", seed=11, seconds=args.seconds)
        synthetic_trip(folder / "other_trip.mp4", seed=97, seconds=args.seconds)
    cases = build(folder, folder / "original_trip.mp4")
    with (folder / "evaluation_manifest.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["case_id", "reference_video", "candidate_video", "scenario", "expected_match"])
        for case_id, candidate, scenario, expected in cases:
            writer.writerow([case_id, "original_trip.mp4", candidate, scenario, expected])
    (folder / "transform_log.json").write_text(json.dumps(LOG, indent=2), encoding="utf-8")
    print(f"Created {len(cases)} cases in {folder.resolve()}")


if __name__ == "__main__":
    main()
