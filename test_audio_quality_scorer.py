"""
test_audio_quality_scorer.py
=======================
基于多特征投票的 TTS 乱码检测器。

用法:
    # 用已标注的样本验证准确率
    python test_audio_quality_scorer.py verify \
        --normal n1.mp3 n2.mp3 n3.mp3 \
        --error e1.mp3 e2.mp3 e3.mp3

    python test_audio_quality_scorer.py verify \
  --normal output/final/normal/normal_article1_seg1.mp3 \
           output/final/normal/normal_article2_seg1.mp3 \
           output/final/normal/normal_article2_seg2.mp3 \
           output/final/normal/normal_article2_seg3.mp3 \
           output/final/normal/normal_seg1.mp3 \
  --error  output/final/error/error_article1_seg1.mp3 \
           output/final/error/error_article1_seg2.mp3 \
           output/final/error/error_article1_seg3.mp3 \
           output/final/error/error_article1_seg4.mp3 \
           output/final/error/error_article2_seg1.mp3 \
           output/final/error/error_article2_seg2.mp3 \
           output/final/error/error_article2_seg3.mp3 \
           output/final/error/error_article2_seg4.mp3        

    # 检测单个文件
    python test_audio_quality_scorer.py check some_audio.mp3
"""

import sys
import struct
import argparse
import numpy as np
from pydub import AudioSegment


# ============================================================
# 特征计算（从 audio_feature_lab.py 精简而来）
# ============================================================

def load_audio(path, sr=16000):
    audio = AudioSegment.from_file(path)
    audio = audio.set_channels(1).set_sample_width(2).set_frame_rate(sr)
    samples = np.array(struct.unpack(f"<{len(audio.raw_data)//2}h", audio.raw_data), dtype=np.float32)
    return samples / 32768.0, sr


def extract_features(audio_path) -> dict:
    """提取用于乱码检测的 5 个核心特征"""
    samples, sr = load_audio(audio_path)
    duration = len(samples) / sr
    feats = {}

    # --- 1. peaks_per_sec: 每秒能量峰值数 ---
    frame_len = int(sr * 0.02)
    hop = frame_len // 2
    energies = []
    for start in range(0, len(samples) - frame_len, hop):
        energies.append(np.sqrt(np.mean(samples[start:start+frame_len] ** 2)))
    energies = np.array(energies)

    peaks = []
    if len(energies) > 10:
        sm = np.convolve(energies, np.ones(5)/5, mode='same')
        thresh = np.mean(sm) * 0.5
        for i in range(1, len(sm)-1):
            if sm[i] > sm[i-1] and sm[i] > sm[i+1] and sm[i] > thresh:
                peaks.append(i)
    feats["peaks_per_sec"] = len(peaks) / duration if duration > 0 else 0

    # 音节间隔
    if len(peaks) >= 3:
        intervals = np.diff(peaks) * 10  # ms
        feats["interval_mean_ms"] = np.mean(intervals)
    else:
        feats["interval_mean_ms"] = 999

    # --- 2 & 3. centroid_mean, flux_mean, flux_cv ---
    frame_spec = int(sr * 0.03)
    hop_spec = frame_spec // 2
    centroids, fluxes = [], []
    prev = None
    for start in range(0, len(samples) - frame_spec, hop_spec):
        frame = samples[start:start+frame_spec]
        spec = np.abs(np.fft.rfft(frame * np.hanning(len(frame))))
        freqs = np.fft.rfftfreq(len(frame), 1.0/sr)
        if np.sum(spec) > 0:
            centroids.append(np.sum(freqs * spec) / np.sum(spec))
            if prev is not None and len(prev) == len(spec):
                fluxes.append(np.sqrt(np.mean((spec - prev)**2)))
            prev = spec

    feats["centroid_mean"] = np.mean(centroids) if centroids else 0
    feats["flux_mean"] = np.mean(fluxes) if fluxes else 0
    feats["flux_cv"] = (np.std(fluxes) / np.mean(fluxes)) if fluxes and np.mean(fluxes) > 0 else 0

    return feats


# ============================================================
# 多特征投票检测器
# ============================================================

# 每个特征的投票规则：(特征名, 方向, 正常均值, 异常均值)
# 方向: "high" = 异常时偏高, "low" = 异常时偏低
# 阈值设在两组均值的中间偏正常一侧（减少漏检）
VOTE_RULES = [
    # 特征              方向    正常均值   异常均值    阈值（偏正常侧 40%）
    ("flux_mean",       "low",  0.49,     0.31,      None),
    ("peaks_per_sec",   "low",  6.62,     5.15,      None),
    ("interval_mean_ms","high", 149.85,   197.97,    None),
    ("flux_cv",         "high", 0.89,     1.04,      None),
    ("centroid_mean",   "high", 1596.66,  1949.13,   None),
]

def compute_thresholds():
    """根据正常和异常均值计算阈值：偏正常侧 40% 处"""
    rules = []
    for name, direction, n_mean, e_mean, _ in VOTE_RULES:
        # 阈值设在 正常均值 + 40% * (异常均值 - 正常均值)
        threshold = n_mean + 0.4 * (e_mean - n_mean)
        rules.append((name, direction, n_mean, e_mean, threshold))
    return rules

RULES = compute_thresholds()


def score_audio(audio_path) -> dict:
    """
    对音频做乱码风险评分。

    Returns:
        {
            "is_suspect": bool,      # 是否可疑
            "votes": int,            # 异常投票数 (0-5)
            "total": int,            # 总规则数
            "details": [str],        # 每条规则的判断
            "features": dict,        # 原始特征值
        }
    """
    feats = extract_features(audio_path)
    votes = 0
    details = []

    for name, direction, n_mean, e_mean, threshold in RULES:
        val = feats.get(name, 0)

        if direction == "high":
            is_abnormal = val > threshold
            detail = f"{'✗' if is_abnormal else '✓'} {name}: {val:.2f} (阈值 {'>' if direction=='high' else '<'}{threshold:.2f}, 正常≈{n_mean:.2f}, 异常≈{e_mean:.2f})"
        else:
            is_abnormal = val < threshold
            detail = f"{'✗' if is_abnormal else '✓'} {name}: {val:.2f} (阈值 {'<' if direction=='low' else '>'}{threshold:.2f}, 正常≈{n_mean:.2f}, 异常≈{e_mean:.2f})"

        if is_abnormal:
            votes += 1
        details.append(detail)

    # 3/5 以上投票才判定为可疑
    is_suspect = votes >= 3

    return {
        "is_suspect": is_suspect,
        "votes": votes,
        "total": len(RULES),
        "details": details,
        "features": feats,
    }


# ============================================================
# 命令行接口
# ============================================================

def cmd_check(args):
    """检测单个音频"""
    result = score_audio(args.file)
    status = "❌ 可疑" if result["is_suspect"] else "✅ 正常"
    print(f"\n  {status}  投票: {result['votes']}/{result['total']}")
    for d in result["details"]:
        print(f"    {d}")


def cmd_verify(args):
    """用已标注样本验证准确率"""
    print(f"\n{'=' * 70}")
    print(f"  验证检测器准确率")
    print(f"  投票阈值: ≥3/5 判定为可疑")
    print(f"{'=' * 70}")

    # 测试所有正常音频
    print(f"\n  --- 正常音频 ({len(args.normal)} 个) ---")
    normal_results = []
    for p in args.normal:
        try:
            r = score_audio(p)
            normal_results.append(r)
            status = "❌ 误报!" if r["is_suspect"] else "✅ 正确"
            print(f"    {status}  {p}  票数={r['votes']}/{r['total']}")
        except Exception as e:
            print(f"    ⚠️ {p}: {e}")

    # 测试所有异常音频
    print(f"\n  --- 异常音频 ({len(args.error)} 个) ---")
    error_results = []
    for p in args.error:
        try:
            r = score_audio(p)
            error_results.append(r)
            status = "✅ 正确" if r["is_suspect"] else "❌ 漏检!"
            print(f"    {status}  {p}  票数={r['votes']}/{r['total']}")
        except Exception as e:
            print(f"    ⚠️ {p}: {e}")

    # 统计
    if normal_results and error_results:
        fp = sum(1 for r in normal_results if r["is_suspect"])
        fn = sum(1 for r in error_results if not r["is_suspect"])
        tp = sum(1 for r in error_results if r["is_suspect"])
        tn = sum(1 for r in normal_results if not r["is_suspect"])

        total = len(normal_results) + len(error_results)
        accuracy = (tp + tn) / total

        print(f"\n{'=' * 70}")
        print(f"  检测结果统计")
        print(f"{'=' * 70}")
        print(f"  准确率:   {accuracy:.1%} ({tp+tn}/{total})")
        print(f"  正确放行: {tn}/{len(normal_results)} 正常音频")
        print(f"  正确拦截: {tp}/{len(error_results)} 异常音频")
        print(f"  误报:     {fp}/{len(normal_results)} (正常被判为异常)")
        print(f"  漏检:     {fn}/{len(error_results)} (异常被判为正常)")

        # 投票分布
        print(f"\n  --- 投票分布 ---")
        print(f"  {'票数':>4s}  {'正常':>6s}  {'异常':>6s}")
        for v in range(6):
            n_count = sum(1 for r in normal_results if r["votes"] == v)
            e_count = sum(1 for r in error_results if r["votes"] == v)
            bar_n = "█" * n_count
            bar_e = "▓" * e_count
            marker = "  ← 阈值线" if v == 3 else ""
            print(f"    {v}/5   {n_count:>4d} {bar_n:<10s}  {e_count:>4d} {bar_e:<10s}{marker}")

        # 建议
        print(f"\n  --- 建议 ---")
        if fp > 0:
            print(f"  ⚠️ 有 {fp} 个误报，可以把阈值从 3 调高到 4")
        if fn > 0:
            print(f"  ⚠️ 有 {fn} 个漏检，可以把阈值从 3 调低到 2")
        if fp == 0 and fn == 0:
            print(f"  🎉 完美！当前阈值下零误报零漏检")
        if fp == 0 and fn > 0:
            print(f"  💡 没有误报，可以尝试把阈值调低到 2 来减少漏检")


def main():
    parser = argparse.ArgumentParser(description="TTS 乱码检测器")
    sub = parser.add_subparsers(dest="cmd")

    p_check = sub.add_parser("check", help="检测单个音频")
    p_check.add_argument("file", help="音频文件路径")

    p_verify = sub.add_parser("verify", help="批量验证准确率")
    p_verify.add_argument("--normal", nargs="+", required=True)
    p_verify.add_argument("--error", nargs="+", required=True)

    args = parser.parse_args()
    if args.cmd == "check":
        cmd_check(args)
    elif args.cmd == "verify":
        cmd_verify(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()