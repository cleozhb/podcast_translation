"""
测试之前出乱码的文本，重新合成一遍，验证是否仍然出错。

用法：
  # 合成全部 4 条问题文本，开启 STT 质量验证
  python test_tts_garbled.py

  # 只合成第 2 条
  python test_tts_garbled.py --index 2

  # 不做质量验证，只合成听一下
  python test_tts_garbled.py --no-verify

  # 自定义文本
  python test_tts_garbled.py --text "你想测试的文本"
"""

import os
import yaml
import argparse
from core.tts_preprocessor import preprocess_for_tts, PreprocessConfig
from providers.cosyvoice_tts import CosyVoiceTTS


# 之前出乱码的 7 条文本
GARBLED_TEXTS = [
    '我想先抛出一个有点"劲爆"的问题——大概六个月前，不知道大家还记得不记得？你先是离开了Anthropic，加入了Cursor，结果两周后又回了Anthropic。',
    '报告里的原话是："就在我们一眨眼的工夫，AI 已经接管了整个软件开发。"',
    '越来越多经验最丰富、资历最深的工程师——包括你本人——都在公开分享一个事实：自己已经不再写代码了，所有代码都是 AI 生成的。',
    '我们走到这一步，很大程度上，就得益于你当初启动的这个小项目，以及你和团队在过去一年里把它一步步做大的努力。所以我很想听听，你对过去这一年，还有你所做的工作所带来的这些影响，有什么样的思考和感受？',
    '另一个部分是文件系统，就像你刚才提到的。那为什么智能体需要文件系统呢？',
    '我觉得主要有两件事发生了：一是模型本身变强了；二是我们开始摸索出一些“基础构件”——也就是所谓“调度框架”，真正让模型能发挥出最佳水平。紧接着，我们就看到一大波人开始动手构建智能体。那么问题来了：最终是模型把框架层给“吃掉”了？还是说，框架和基础设施层反过来把模型给“消化”掉了？我的看法是——调度框架才是最关键的。云上的大模型确实很厉害，但真正让它们跑起来、干成事的，其实是这个调度框架。',
    '所以，大多数规划类工具本质上就是一个待办任务清单。每个任务通常包含一段描述、一个状态——这两项算是最核心的信息。状态可以是“已完成”“正在处理中”，或者“将来再做”，当然你也可以自定义成别的形式，但这是目前我们见到的最常见模式。  \
然后呢，绝大多数框架其实并不会强制你去执行这个计划。它只是把计划放进去、帮你跟踪一下进度而已，并不会主动把计划拆解开来，明确告诉你：“好，你已经制定了这个计划，现在咱们先干第一件事；等第一件事做完，再自动跳到第二件事。”  \
以前大模型没那么强的时候，确实是这么做的：先有一个明确的规划步骤，生成完整计划；然后再交给另一个智能体（agent），让它专门去执行第一步；执行完再返回来，继续下一步……但这样会冒出一大堆边缘情况。比如，计划执行到一半，突然发现得调整——那是不是得加一步：先检查一下，“我该不该重新调整计划？”这样一来，整个流程就变得特别绕、特别复杂。  \
所以现在主流的做法是：把计划就放在一个文本文件里，主智能体可以参考它来辅助决策，但不会严格地、显式地标注“我现在正在执行第几步”或者“接下来必须执行哪一步”。',
]


def main():
    parser = argparse.ArgumentParser(description="测试之前出乱码的文本 TTS 合成")
    parser.add_argument("--index", type=int, help="只测试第 N 条 (1-4)")
    parser.add_argument("--text", type=str, help="自定义测试文本")
    parser.add_argument("--voice-url", type=str,
                        default="https://cleo-oss-bucket.oss-cn-wulanchabu.aliyuncs.com/podcast-voiceprints/The_MAD_Podcast_with_Matt_Turck_SPEAKER_00_voiceprint.wav",
                        help="声纹音频 URL（OSS 地址或 voice_id）")
    parser.add_argument("--no-preprocess", action="store_true", help="跳过文本预处理，直接送原文")
    parser.add_argument("--no-verify", action="store_true", help="跳过 STT 质量验证")
    parser.add_argument("--threshold", type=float, default=0.8, help="STT 相似度阈值")
    parser.add_argument("--output-dir", type=str, default="output/test_garbled", help="输出目录")
    args = parser.parse_args()

    # 准备测试文本
    if args.text:
        texts = [args.text]
    elif args.index:
        if 1 <= args.index <= len(GARBLED_TEXTS):
            texts = [GARBLED_TEXTS[args.index - 1]]
        else:
            print(f"❌ --index 范围 1-{len(GARBLED_TEXTS)}")
            return
    else:
        texts = GARBLED_TEXTS

    # 加载配置
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)

    # 使用 config.yaml 中的模型（和正式环境一致）
    config.setdefault("cosyvoice", {})
    config["cosyvoice"].setdefault("quality_verify", {})
    verify = not args.no_verify
    config["cosyvoice"]["quality_verify"]["enabled"] = verify
    config["cosyvoice"]["quality_verify"]["similarity_threshold"] = args.threshold
    config["cosyvoice"]["quality_verify"]["max_retries"] = 3

    tts = CosyVoiceTTS(config)
    tts_pre_cfg = config.get("tts_preprocess", {})
    preprocess_cfg = PreprocessConfig(
        max_sentence_chars=tts_pre_cfg.get("max_sentence_chars", 80),
        custom_word_map=tts_pre_cfg.get("custom_word_map"),
    )

    # 创建输出目录
    os.makedirs(args.output_dir, exist_ok=True)

    print("=" * 60)
    print(f"  TTS 乱码复现测试")
    print(f"  模型: {config['cosyvoice'].get('model')}")
    print(f"  音色: {args.voice_url[:60]}...")
    print(f"  共 {len(texts)} 条，质量验证: {'开' if verify else '关'}")
    print(f"  预处理: {'开' if not args.no_preprocess else '关'}")
    print(f"  输出目录: {args.output_dir}")
    print("=" * 60)

    results = []

    for i, raw_text in enumerate(texts, 1):
        print(f"\n{'─' * 60}")
        print(f"  [{i}/{len(texts)}]")
        print(f"  原文: {raw_text[:80]}{'...' if len(raw_text) > 80 else ''}")

        # 预处理
        if args.no_preprocess:
            tts_text = raw_text
        else:
            tts_text = preprocess_for_tts(raw_text, preprocess_cfg)
            if tts_text != raw_text:
                print(f"  预处理后: {tts_text[:]}{'...' if len(tts_text) > 80 else ''}")

        # 合成
        output_path = os.path.join(args.output_dir, f"garbled_test_{i}.mp3")
        try:
            result = tts.synthesize(tts_text, output_path, voice_url=args.voice_url)
            warning = getattr(result, "quality_warning", None)
            if warning:
                status = f"⚠️ 质量警告: {warning}"
            else:
                status = "✅ 通过"
            results.append((i, status, output_path))
            print(f"  结果: {status}")
        except Exception as e:
            status = f"❌ 失败: {e}"
            results.append((i, status, None))
            print(f"  结果: {status}")

    # 汇总
    print(f"\n{'=' * 60}")
    print(f"  汇总")
    print(f"{'=' * 60}")
    for idx, status, path in results:
        print(f"  [{idx}] {status}")
        if path:
            print(f"       {path}")
    print()


if __name__ == "__main__":
    main()
