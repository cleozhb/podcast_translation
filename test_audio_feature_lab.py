"""
test_audio_feature_lab.py
====================
批量对比正常/异常音频的声学特征，找出有区分力的指标。
支持任意长度、任意数量的音频。

用法:
    python test_audio_feature_lab.py --normal n1.mp3 n2.mp3 n3.mp3 --error e1.mp3 e2.mp3 e3.mp3

    python test_audio_feature_lab.py --normal output/final/normal/normal_article1_seg1.mp3 output/final/normal/normal_article2_seg1.mp3 output/final/normal/normal_article2_seg2.mp3 output/final/normal/normal_article2_seg3.mp3 output/final/normal/normal_seg1.mp3 --error output/final/error/error_article1_seg1.mp3 output/final/error/error_article1_seg2.mp3 output/final/error/error_article1_seg3.mp3 output/final/error/error_article1_seg4.mp3 output/final/error/error_article2_seg1.mp3 output/final/error/error_article2_seg2.mp3 output/final/error/error_article2_seg3.mp3 output/final/error/error_article2_seg4.mp3
每组至少 1 个文件，建议各 3-5 个以上效果更好。
"""

import sys
import struct
import math
import argparse
import numpy as np
from pydub import AudioSegment


def load_audio(path: str, sr: int = 16000) -> tuple[np.ndarray, int]:
    audio = AudioSegment.from_file(path)
    audio = audio.set_channels(1).set_sample_width(2).set_frame_rate(sr)
    samples = np.array(struct.unpack(f"<{len(audio.raw_data) // 2}h", audio.raw_data), dtype=np.float32)
    samples = samples / 32768.0
    return samples, sr


def compute_pitch_contour(samples, sr, frame_ms=30, hop_ms=10):
    frame_len = int(sr * frame_ms / 1000)
    hop_len = int(sr * hop_ms / 1000)
    min_lag = int(sr / 400)
    max_lag = int(sr / 80)
    pitches = []
    for start in range(0, len(samples) - frame_len, hop_len):
        frame = samples[start:start + frame_len]
        corr = np.correlate(frame, frame, mode='full')
        corr = corr[len(corr) // 2:]
        if max_lag <= len(corr):
            search = corr[min_lag:max_lag]
            if len(search) > 0 and np.max(search) > 0.3 * corr[0]:
                peak = np.argmax(search) + min_lag
                pitches.append(sr / peak)
            else:
                pitches.append(0)
        else:
            pitches.append(0)
    return np.array(pitches)


def compute_all_features(samples, sr) -> dict:
    """计算所有特征（全部归一化为与时长无关的指标）"""
    duration = len(samples) / sr
    features = {"duration": duration}

    # ===== 基频 =====
    pitches = compute_pitch_contour(samples, sr)
    voiced = pitches[pitches > 0]

    features["voiced_ratio"] = len(voiced) / len(pitches) if len(pitches) > 0 else 0

    if len(voiced) >= 5:
        features["f0_mean"] = np.mean(voiced)
        features["f0_std"] = np.std(voiced)
        features["f0_cv"] = features["f0_std"] / features["f0_mean"] if features["f0_mean"] > 0 else 0
        f0_diff = np.abs(np.diff(voiced))
        features["f0_diff_mean"] = np.mean(f0_diff)
        features["f0_diff_std"] = np.std(f0_diff)
        features["f0_jump_ratio"] = np.sum(f0_diff > 50) / len(f0_diff)
    else:
        for k in ["f0_mean", "f0_std", "f0_cv", "f0_diff_mean", "f0_diff_std", "f0_jump_ratio"]:
            features[k] = 0

    # ===== 音节节奏（归一化为 per-second） =====
    frame_ms = 20
    frame_len = int(sr * frame_ms / 1000)
    hop_len = frame_len // 2
    energies = []
    for start in range(0, len(samples) - frame_len, hop_len):
        frame = samples[start:start + frame_len]
        energies.append(np.sqrt(np.mean(frame ** 2)))
    energies = np.array(energies)

    if len(energies) > 10:
        kernel = np.ones(5) / 5
        smoothed = np.convolve(energies, kernel, mode='same')
        peaks = []
        for i in range(1, len(smoothed) - 1):
            if smoothed[i] > smoothed[i - 1] and smoothed[i] > smoothed[i + 1]:
                if smoothed[i] > np.mean(smoothed) * 0.5:
                    peaks.append(i)

        # 每秒峰值数（时长归一化）
        features["peaks_per_sec"] = len(peaks) / duration if duration > 0 else 0

        if len(peaks) >= 3:
            intervals = np.diff(peaks) * (frame_ms / 2)
            features["interval_mean_ms"] = np.mean(intervals)
            features["interval_cv"] = np.std(intervals) / np.mean(intervals) if np.mean(intervals) > 0 else 0
            features["interval_regularity"] = 1.0 / (1.0 + features["interval_cv"])
        else:
            features["peaks_per_sec"] = 0
            features["interval_mean_ms"] = 0
            features["interval_cv"] = 0
            features["interval_regularity"] = 0
    else:
        for k in ["peaks_per_sec", "interval_mean_ms", "interval_cv", "interval_regularity"]:
            features[k] = 0

    # ===== 频谱特征 =====
    frame_len_spec = int(sr * 0.03)
    hop_spec = frame_len_spec // 2
    centroids, flatnesses, fluxes = [], [], []
    prev_spec = None

    for start in range(0, len(samples) - frame_len_spec, hop_spec):
        frame = samples[start:start + frame_len_spec]
        window = np.hanning(len(frame))
        spectrum = np.abs(np.fft.rfft(frame * window))
        freqs = np.fft.rfftfreq(len(frame), 1.0 / sr)

        if np.sum(spectrum) > 0:
            centroids.append(np.sum(freqs * spectrum) / np.sum(spectrum))
            log_spec = np.log(spectrum + 1e-10)
            flatnesses.append(np.exp(np.mean(log_spec)) / (np.mean(spectrum) + 1e-10))
            if prev_spec is not None and len(prev_spec) == len(spectrum):
                fluxes.append(np.sqrt(np.mean((spectrum - prev_spec) ** 2)))
            prev_spec = spectrum

    features["centroid_mean"] = np.mean(centroids) if centroids else 0
    features["centroid_std"] = np.std(centroids) if centroids else 0
    features["centroid_cv"] = features["centroid_std"] / features["centroid_mean"] if features["centroid_mean"] > 0 else 0
    features["flatness_mean"] = np.mean(flatnesses) if flatnesses else 0
    features["flatness_std"] = np.std(flatnesses) if flatnesses else 0
    features["flux_mean"] = np.mean(fluxes) if fluxes else 0
    features["flux_std"] = np.std(fluxes) if fluxes else 0
    features["flux_cv"] = features["flux_std"] / features["flux_mean"] if features["flux_mean"] > 0 else 0

    return features


def analyze_batch(paths, label):
    """批量分析，返回每个特征的 [值1, 值2, ...] """
    all_feats = []
    for p in paths:
        try:
            samples, sr = load_audio(p)
            feats = compute_all_features(samples, sr)
            all_feats.append(feats)
            dur = feats["duration"]
            print(f"    ✓ {p} ({dur:.1f}s)")
        except Exception as e:
            print(f"    ✗ {p}: {e}")
    return all_feats


def main():
    parser = argparse.ArgumentParser(description="批量对比正常/异常音频特征")
    parser.add_argument("--normal", nargs="+", required=True, help="正常音频文件列表")
    parser.add_argument("--error", nargs="+", required=True, help="异常音频文件列表")
    args = parser.parse_args()

    print(f"\n  ✅ 正常音频 ({len(args.normal)} 个):")
    normal_feats = analyze_batch(args.normal, "normal")

    print(f"\n  ❌ 异常音频 ({len(args.error)} 个):")
    error_feats = analyze_batch(args.error, "error")

    if not normal_feats or not error_feats:
        print("\n  ❌ 需要至少各一个有效音频")
        return

    # 汇总：每个特征的均值和标准差
    keys = [k for k in normal_feats[0] if k != "duration"]

    print(f"\n{'=' * 80}")
    print(f"  特征分布对比 (正常 {len(normal_feats)} 个 vs 异常 {len(error_feats)} 个)")
    print(f"{'=' * 80}")
    print(f"  {'特征':<22s} {'正常 μ±σ':>18s} {'异常 μ±σ':>18s} {'重叠?':>6s} {'区分力':>8s}")
    print(f"  {'─' * 76}")

    results = []
    for k in keys:
        n_vals = np.array([f[k] for f in normal_feats])
        e_vals = np.array([f[k] for f in error_feats])

        n_mean, n_std = np.mean(n_vals), np.std(n_vals)
        e_mean, e_std = np.mean(e_vals), np.std(e_vals)

        # 判断分布是否重叠
        # 用均值差距 vs 两组标准差之和的比率
        pooled_std = n_std + e_std + 1e-10
        separation = abs(e_mean - n_mean) / pooled_std

        # Cohen's d（效应量）
        pooled_var = ((n_std ** 2 + e_std ** 2) / 2) if (n_std > 0 or e_std > 0) else 1e-10
        cohens_d = abs(e_mean - n_mean) / math.sqrt(pooled_var) if pooled_var > 0 else 0

        # 区分力评级
        if len(normal_feats) < 3 or len(error_feats) < 3:
            # 样本太少，用分离度粗略判断
            if separation > 1.5:
                grade = "★★★"
            elif separation > 0.8:
                grade = "★★"
            elif separation > 0.4:
                grade = "★"
            else:
                grade = ""
        else:
            # 样本够，用 Cohen's d
            if cohens_d > 2.0:
                grade = "★★★"
            elif cohens_d > 1.0:
                grade = "★★"
            elif cohens_d > 0.5:
                grade = "★"
            else:
                grade = ""

        # 是否重叠
        n_lo, n_hi = n_mean - n_std, n_mean + n_std
        e_lo, e_hi = e_mean - e_std, e_mean + e_std
        overlaps = not (n_hi < e_lo or e_hi < n_lo)
        overlap_str = "重叠" if overlaps else "分离"

        n_str = f"{n_mean:.2f}±{n_std:.2f}"
        e_str = f"{e_mean:.2f}±{e_std:.2f}"

        direction = "↑" if e_mean > n_mean else "↓"

        print(f"  {k:<22s} {n_str:>18s} {e_str:>18s} {overlap_str:>6s} {grade:>4s} {direction}")

        results.append((k, separation, cohens_d, grade, n_mean, e_mean, overlaps))

    # 排名
    results.sort(key=lambda x: -x[1])
    print(f"\n  --- 区分力排名 ---")
    for i, (k, sep, cd, grade, nm, em, ovl) in enumerate(results[:12], 1):
        direction = "异常偏高" if em > nm else "异常偏低"
        bar = "█" * int(min(sep, 3.0) * 10)
        ovl_mark = "" if not ovl else " (有重叠)"
        print(f"  {i:2d}. {k:<22s} 分离度={sep:.2f}  Cohen's d={cd:.2f}  {grade:<4s} {direction}{ovl_mark}  {bar}")

    # 给出建议
    good_features = [(k, sep, nm, em) for k, sep, cd, grade, nm, em, ovl in results if sep > 0.6 and not ovl]
    ok_features = [(k, sep, nm, em) for k, sep, cd, grade, nm, em, ovl in results if sep > 0.4]

    print(f"\n{'=' * 80}")
    if good_features:
        print(f"  🎯 推荐用于检测的特征（分布无重叠，分离度 > 0.6）:")
        for k, sep, nm, em in good_features:
            direction = ">" if em > nm else "<"
            mid = (nm + em) / 2
            print(f"     {k}: 正常≈{nm:.2f}, 异常≈{em:.2f}, 建议阈值≈{mid:.2f}")
    elif ok_features:
        print(f"  ⚠️ 有一定区分力但分布有重叠的特征（需要更多样本确认）:")
        for k, sep, nm, em in ok_features[:5]:
            print(f"     {k}: 分离度={sep:.2f}")
    else:
        print(f"  ❌ 未找到有足够区分力的特征。")
        print(f"     建议：增加样本数量，或确认截取的异常片段是否足够纯粹。")

    print(f"\n  💡 建议多截几段纯异常和纯正常音频（各 5 段以上），结果会更可靠。")


if __name__ == "__main__":
    main()