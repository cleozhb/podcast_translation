"""
Experimental TTS quality analysis for human-labeled normal/garbled samples.

This module is used by the standalone wrapper `tts_quality_lab.py`.
It intentionally does not decide production retries. Use it to validate whether
local window acoustic features match human-labeled garbled audio before wiring
any detector into the main TTS pipeline.

See `tts_quality_lab.py` for command-line usage and input/output examples.

`tts_quality_lab.py` 是一个**离线实验脚本**，目前**不会被主翻译/TTS 流程自动调用**。

它的作用是验证这件事：你人工听出来的“非中非英乱码”，能不能被某些**局部声学特征**稳定区分出来。之前整段声学特征看不出差异，所以我没有把它接入生产重试，而是先做成 lab 工具。

用法大概是：
**必须提供的只有音频文件**：

```text
samples/
  normal/
    ok1.mp3
  garbled/
    bad1.mp3
```

`.txt` 是可选的同名 sidecar，用来记录这段 TTS 当时的输入文本：

```text
samples/
  normal/
    ok1.mp3
    ok1.txt
  garbled/
    bad1.mp3
    bad1.txt
```

有 `.txt` 时，脚本会额外判断这段文本是否含高风险符号，比如 `[SPEAKER_00]`、HTML、URL、括号、emoji 等。没有 `.txt` 也能正常跑声学分析，只是 `text_risk` 相关字段会是空/false。

目录名 `normal/` 和 `garbled/` 也建议保留，因为脚本靠它们知道哪些是人工标注正常、哪些是人工标注乱码。

然后跑：

```bash
uv run python tts_quality_lab.py samples \
  --output-dir output/tts_quality_lab \
  --export-windows
```

它会输出：
- `tts_quality_lab_report.json`：每个音频的整段特征和最异常窗口
- `tts_quality_lab_samples.csv`：每个样本的摘要
- `tts_quality_lab_feature_separation.csv`：哪些特征最能区分 normal/garbled
- `windows/`：最可疑的局部音频切片，方便你快速抽听

所以它现在的定位是：**验证检测方法是否靠谱**。只有当你用人工标注样本跑出来发现局部特征确实有效，才值得把它接入自动重试；否则它就只是分析工具，不影响生产链路。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pydub import AudioSegment

from core.tts_preprocessor import contains_high_risk_tts_chars


AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}


@dataclass
class SampleReport:
    label: str
    audio_path: str
    text_path: str
    text_risk: bool
    duration: float
    whole_features: dict
    worst_window: dict


def load_audio(path: str, sr: int = 16000) -> tuple[np.ndarray, int, AudioSegment]:
    audio = AudioSegment.from_file(path)
    audio = audio.set_channels(1).set_sample_width(2).set_frame_rate(sr)
    samples = np.array(
        struct.unpack(f"<{len(audio.raw_data) // 2}h", audio.raw_data),
        dtype=np.float32,
    )
    return samples / 32768.0, sr, audio


def _pitch_contour(samples: np.ndarray, sr: int, frame_ms: int = 30, hop_ms: int = 15) -> np.ndarray:
    frame_len = int(sr * frame_ms / 1000)
    hop_len = int(sr * hop_ms / 1000)
    min_lag = int(sr / 420)
    max_lag = int(sr / 70)
    pitches = []
    for start in range(0, max(0, len(samples) - frame_len), hop_len):
        frame = samples[start:start + frame_len]
        if np.sqrt(np.mean(frame ** 2)) < 0.01:
            pitches.append(0.0)
            continue
        corr = np.correlate(frame, frame, mode="full")
        corr = corr[len(corr) // 2:]
        if max_lag >= len(corr) or corr[0] <= 0:
            pitches.append(0.0)
            continue
        search = corr[min_lag:max_lag]
        peak = int(np.argmax(search)) + min_lag
        if search[peak - min_lag] > 0.25 * corr[0]:
            pitches.append(sr / peak)
        else:
            pitches.append(0.0)
    return np.array(pitches)


def acoustic_features(samples: np.ndarray, sr: int) -> dict:
    duration = len(samples) / sr if sr else 0.0
    if len(samples) < int(sr * 0.05):
        return {
            "duration": duration,
            "rms": 0.0,
            "voiced_ratio": 0.0,
            "pitch_jump_ratio": 0.0,
            "phoneme_rate_proxy": 0.0,
            "spectral_flatness": 0.0,
            "high_freq_ratio": 0.0,
            "burst_ratio": 0.0,
            "tail_burst": 0.0,
        }

    rms = float(np.sqrt(np.mean(samples ** 2)))
    pitches = _pitch_contour(samples, sr)
    voiced = pitches[pitches > 0]
    voiced_ratio = float(len(voiced) / len(pitches)) if len(pitches) else 0.0
    if len(voiced) >= 3:
        diffs = np.abs(np.diff(voiced))
        pitch_jump_ratio = float(np.mean(diffs > 55.0))
    else:
        pitch_jump_ratio = 0.0

    frame_len = int(sr * 0.02)
    hop = max(1, frame_len // 2)
    energies = []
    for start in range(0, len(samples) - frame_len, hop):
        energies.append(float(np.sqrt(np.mean(samples[start:start + frame_len] ** 2))))
    energies = np.array(energies)
    if len(energies) > 4:
        threshold = max(float(np.mean(energies) * 0.55), 0.01)
        peaks = [
            i for i in range(1, len(energies) - 1)
            if energies[i] > energies[i - 1] and energies[i] > energies[i + 1] and energies[i] > threshold
        ]
        phoneme_rate_proxy = float(len(peaks) / max(duration, 0.001))
        burst_ratio = float(np.mean(energies > (np.mean(energies) + 2.5 * np.std(energies))))
    else:
        phoneme_rate_proxy = 0.0
        burst_ratio = 0.0

    frame_spec = int(sr * 0.03)
    hop_spec = max(1, frame_spec // 2)
    flatnesses = []
    high_ratios = []
    for start in range(0, len(samples) - frame_spec, hop_spec):
        frame = samples[start:start + frame_spec] * np.hanning(frame_spec)
        spectrum = np.abs(np.fft.rfft(frame)) + 1e-10
        freqs = np.fft.rfftfreq(frame_spec, 1.0 / sr)
        flatnesses.append(float(np.exp(np.mean(np.log(spectrum))) / np.mean(spectrum)))
        high = np.sum(spectrum[freqs >= 3500])
        total = np.sum(spectrum)
        high_ratios.append(float(high / total) if total > 0 else 0.0)

    tail = samples[-int(sr * 0.45):] if len(samples) > int(sr * 0.45) else samples
    head = samples[:-int(sr * 0.45)] if len(samples) > int(sr * 0.9) else samples
    tail_rms = float(np.sqrt(np.mean(tail ** 2))) if len(tail) else 0.0
    head_rms = float(np.sqrt(np.mean(head ** 2))) if len(head) else 0.0
    tail_burst = tail_rms / max(head_rms, 1e-6)

    return {
        "duration": duration,
        "rms": rms,
        "voiced_ratio": voiced_ratio,
        "pitch_jump_ratio": pitch_jump_ratio,
        "phoneme_rate_proxy": phoneme_rate_proxy,
        "spectral_flatness": float(np.mean(flatnesses)) if flatnesses else 0.0,
        "high_freq_ratio": float(np.mean(high_ratios)) if high_ratios else 0.0,
        "burst_ratio": burst_ratio,
        "tail_burst": tail_burst,
    }


def window_reports(samples: np.ndarray, sr: int, window_sec: float = 0.8, hop_sec: float = 0.25) -> list[dict]:
    window = int(sr * window_sec)
    hop = int(sr * hop_sec)
    reports = []
    if len(samples) < window:
        feats = acoustic_features(samples, sr)
        feats.update({"start": 0.0, "end": len(samples) / sr, "score": anomaly_score(feats)})
        return [feats]
    for start in range(0, len(samples) - window + 1, hop):
        chunk = samples[start:start + window]
        feats = acoustic_features(chunk, sr)
        feats.update({
            "start": start / sr,
            "end": (start + window) / sr,
            "score": anomaly_score(feats),
        })
        reports.append(feats)
    return reports


def anomaly_score(features: dict) -> float:
    """Exploratory score only; tune from labeled reports before production use."""
    score = 0.0
    score += min(1.0, features.get("spectral_flatness", 0.0) / 0.35) * 0.22
    score += min(1.0, features.get("high_freq_ratio", 0.0) / 0.20) * 0.18
    score += min(1.0, features.get("pitch_jump_ratio", 0.0) / 0.35) * 0.20
    score += min(1.0, max(0.0, features.get("phoneme_rate_proxy", 0.0) - 9.0) / 8.0) * 0.16
    score += min(1.0, features.get("burst_ratio", 0.0) / 0.15) * 0.12
    score += min(1.0, max(0.0, features.get("tail_burst", 0.0) - 1.5) / 3.0) * 0.12
    return round(score, 4)


def _read_sidecar_text(audio_path: Path) -> tuple[str, str]:
    txt_path = audio_path.with_suffix(".txt")
    if not txt_path.exists():
        return "", ""
    return txt_path.read_text(encoding="utf-8"), str(txt_path)


def collect_samples(root: str) -> list[tuple[str, Path]]:
    samples = []
    for label in ("normal", "garbled"):
        folder = Path(root) / label
        if not folder.exists():
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() in AUDIO_EXTS:
                samples.append((label, path))
    return samples


def analyze_sample(label: str, path: Path, export_windows: bool, output_dir: Path) -> SampleReport:
    samples, sr, audio = load_audio(str(path))
    text, text_path = _read_sidecar_text(path)
    whole = acoustic_features(samples, sr)
    windows = window_reports(samples, sr)
    worst = max(windows, key=lambda x: x["score"]) if windows else {}

    if export_windows and worst:
        clip_dir = output_dir / "windows" / label
        clip_dir.mkdir(parents=True, exist_ok=True)
        start_ms = int(worst["start"] * 1000)
        end_ms = int(worst["end"] * 1000)
        audio[start_ms:end_ms].export(clip_dir / f"{path.stem}_worst.mp3", format="mp3")

    return SampleReport(
        label=label,
        audio_path=str(path),
        text_path=text_path,
        text_risk=contains_high_risk_tts_chars(text) if text else False,
        duration=whole["duration"],
        whole_features=whole,
        worst_window=worst,
    )


def separation_table(reports: list[SampleReport]) -> list[dict]:
    keys = sorted(reports[0].whole_features.keys()) if reports else []
    rows = []
    for scope in ("whole", "window"):
        for key in keys:
            if key == "duration":
                continue
            normal = []
            garbled = []
            for r in reports:
                source = r.whole_features if scope == "whole" else r.worst_window
                value = float(source.get(key, 0.0))
                (normal if r.label == "normal" else garbled).append(value)
            if not normal or not garbled:
                continue
            n_mean = float(np.mean(normal))
            g_mean = float(np.mean(garbled))
            pooled = math.sqrt(float(np.var(normal) + np.var(garbled)) / 2.0) or 1e-9
            rows.append({
                "scope": scope,
                "feature": key,
                "normal_mean": n_mean,
                "garbled_mean": g_mean,
                "effect_size": abs(g_mean - n_mean) / pooled,
            })
    return sorted(rows, key=lambda x: -x["effect_size"])


def write_reports(reports: list[SampleReport], out_dir: str) -> None:
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_rows = [
        {
            "label": r.label,
            "audio_path": r.audio_path,
            "text_path": r.text_path,
            "text_risk": r.text_risk,
            "duration": r.duration,
            "whole_features": r.whole_features,
            "worst_window": r.worst_window,
        }
        for r in reports
    ]
    (output_dir / "tts_quality_lab_report.json").write_text(
        json.dumps(json_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with open(output_dir / "tts_quality_lab_samples.csv", "w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "label", "audio_path", "text_risk", "duration",
            "worst_start", "worst_end", "worst_score",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in reports:
            writer.writerow({
                "label": r.label,
                "audio_path": r.audio_path,
                "text_risk": r.text_risk,
                "duration": f"{r.duration:.3f}",
                "worst_start": f"{r.worst_window.get('start', 0.0):.3f}",
                "worst_end": f"{r.worst_window.get('end', 0.0):.3f}",
                "worst_score": f"{r.worst_window.get('score', 0.0):.4f}",
            })

    rows = separation_table(reports)
    with open(output_dir / "tts_quality_lab_feature_separation.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["scope", "feature", "normal_mean", "garbled_mean", "effect_size"],
        )
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze labeled TTS normal/garbled samples.")
    parser.add_argument("input_dir", help="Directory containing normal/ and garbled/ subdirectories.")
    parser.add_argument("--output-dir", default="output/tts_quality_lab")
    parser.add_argument("--export-windows", action="store_true")
    args = parser.parse_args(argv)

    samples = collect_samples(args.input_dir)
    if not samples:
        raise SystemExit("No audio samples found. Expected normal/ and/or garbled/ subdirectories.")

    output_dir = Path(args.output_dir)
    reports = [
        analyze_sample(label, path, args.export_windows, output_dir)
        for label, path in samples
    ]
    write_reports(reports, args.output_dir)

    print(f"Analyzed {len(reports)} samples.")
    print(f"Report: {Path(args.output_dir) / 'tts_quality_lab_report.json'}")
    print(f"Separation: {Path(args.output_dir) / 'tts_quality_lab_feature_separation.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
