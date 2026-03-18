"""
测试 TTS 质量验证（STT 反向验证）

用法：
  python test_quality_verify.py /path/to/audio.mp3 "对应的原文文本"

  # 完整命令示例
  异常:
  python test_quality_verify.py output/final/error/error_seg1_50_percent.mp3 "$(cat output/final/error/error_seg1_50_percent.txt)"

  正常:
  python test_quality_verify.py output/final/normal/normal_seg1.mp3 "$(cat output/final/normal/normal_seg1.txt)"
"""

import yaml
from providers.cosyvoice_tts import CosyVoiceTTS


def main():
    import argparse
    parser = argparse.ArgumentParser(description="测试 TTS STT 反向验证")
    parser.add_argument("audio_path", help="本地 mp3 文件路径")
    parser.add_argument("text", nargs="?",
                        default="这是一段测试文本，用于验证音频质量检测功能是否正常工作",
                        help="原始文本（默认用一段示例文本）")
    parser.add_argument("--threshold", type=float, default=0.8,
                        help="STT 相似度阈值（默认 0.8）")
    args = parser.parse_args()

    # 加载配置，构造 CosyVoiceTTS 实例
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)

    # 强制开启质量验证
    config.setdefault("cosyvoice", {})
    config["cosyvoice"].setdefault("quality_verify", {})
    config["cosyvoice"]["quality_verify"]["enabled"] = True
    config["cosyvoice"]["quality_verify"]["similarity_threshold"] = args.threshold

    tts = CosyVoiceTTS(config)

    print("=" * 60)
    print(f"音频文件: {args.audio_path}")
    print(f"原始文本: {args.text[:50]}{'...' if len(args.text) > 50 else ''}")
    print(f"STT 阈值: {args.threshold}")
    print("=" * 60)

    # --- STT 反向验证 ---
    print("\n--- STT 反向验证 ---")
    try:
        recognized = tts._quick_stt(args.audio_path)
        print(f"  识别文本: {recognized[:100]}{'...' if len(recognized) > 100 else ''}")
        if recognized:
            sim = tts._text_similarity(args.text, recognized)
            status = "✅ 通过" if sim >= args.threshold else "❌ 不通过"
            print(f"  相似度: {sim:.3f} (阈值 {args.threshold}) → {status}")
        else:
            print(f"  ❌ STT 未识别出任何文字")
    except Exception as e:
        print(f"  ❌ STT 调用失败: {e}")

    # _verify_quality 综合结果
    print("\n--- _verify_quality 综合结果 ---")
    try:
        is_ok, score, detail = tts._verify_quality(args.text, args.audio_path)
        status = "✅ 通过" if is_ok else "❌ 不通过"
        print(f"  结果: {status}")
        print(f"  分数: {score:.3f}")
        print(f"  详情: {detail}")
    except Exception as e:
        print(f"  ❌ 验证失败: {e}")

    print()


if __name__ == "__main__":
    main()
