"""
Generate a reproducible TTS model comparison run.

Purpose:
  Produce side-by-side TTS samples and a rating sheet for checking garbled audio,
  voice similarity, Chinese naturalness, and tail artifacts across models.

Provider support:
  - Executable now: CosyVoice v3.5-flash and CosyVoice v3.5-plus.
  - Manifest/rating placeholders now: MiniMax, Cartesia, ElevenLabs, Fish Audio.
    Their API keys live under tts_model_compare in config.yaml, but their HTTP
    calls are not implemented in this script yet.

Required input:
  --config config.yaml
    Must contain cosyvoice.api_key for executable CosyVoice runs. Optional
    tts_model_compare.* API keys are copied into the manifest status.
  --cases cases.txt or cases.json
    cases.txt: one test sentence per non-empty line.
    cases.json: list of {"id": "...", "text": "..."} objects.
  --voice-url
    CosyVoice OSS voiceprint URL or existing cosyvoice voice_id. Optional, but
    needed if you want cloned voice instead of the default preset voice.

Create a simple cases file:
  mkdir -p output/tts_compare_cases
  cat > output/tts_compare_cases/cases.txt <<'EOF'
  [SPEAKER_00]: 我想先抛出一个有点“劲爆”的问题——大概六个月前，不知道大家还记得不记得？
  我觉得主要有两件事发生了：一是模型本身变强了；二是我们开始摸索出一些“基础构件”。
  所以，大多数规划类工具本质上就是一个待办任务清单。
  EOF

Run executable comparison:
  uv run python scripts/compare_tts_models.py \
    --config config.yaml \
    --cases output/tts_compare_cases/cases.txt \
    --voice-url "OSS_URL_OR_COSYVOICE_VOICE_ID" \
    --output-dir output/tts_model_compare

Dry run, no API calls:
  uv run python scripts/compare_tts_models.py \
    --config config.yaml \
    --cases output/tts_compare_cases/cases.txt \
    --output-dir output/tts_model_compare \
    --dry-run

Output:
  output/tts_model_compare/manifest.json
  output/tts_model_compare/rating_template.csv
  output/tts_model_compare/*_cosyvoice_*.mp3  # when executable generation runs

Manual review:
  Listen to generated MP3 files and fill rating_template.csv columns:
  garbled_heard, voice_similarity_1_5, chinese_naturalness_1_5,
  tail_artifact_heard, notes.


用新增的 [scripts/compare_tts_models.py](/Users/zhanghuibin/llm/podcast_translation/scripts/compare_tts_models.py) 跑。它现在可以直接生成 **CosyVoice v3.5-flash / v3.5-plus** 的对比音频，并给 MiniMax、Cartesia、ElevenLabs、Fish Audio 生成统一评分表占位。

准备一个测试文本文件，比如：

```bash
mkdir -p output/tts_compare_cases
cat > output/tts_compare_cases/cases.txt <<'EOF'
[SPEAKER_00]: 我想先抛出一个有点“劲爆”的问题——大概六个月前，不知道大家还记得不记得？
我觉得主要有两件事发生了：一是模型本身变强了；二是我们开始摸索出一些“基础构件”。
所以，大多数规划类工具本质上就是一个待办任务清单。
EOF
```

然后跑：

```bash
uv run python scripts/compare_tts_models.py \
  --config config.yaml \
  --cases output/tts_compare_cases/cases.txt \
  --voice-url "你的声纹OSS URL或cosyvoice voice_id" \
  --output-dir output/tts_model_compare
```

输出会在：

```text
output/tts_model_compare/
  manifest.json
  rating_template.csv
  1_cosyvoice_cosyvoice-v3.5-flash.mp3
  1_cosyvoice_cosyvoice-v3.5-plus.mp3
  ...
```

你主要听生成的 mp3，然后在 `rating_template.csv` 里填：
- `garbled_heard`
- `voice_similarity_1_5`
- `chinese_naturalness_1_5`
- `tail_artifact_heard`
- `notes`

如果你只是想先生成评分模板、不调用 API：

```bash
uv run python scripts/compare_tts_models.py \
  --config config.yaml \
  --cases output/tts_compare_cases/cases.txt \
  --output-dir output/tts_model_compare \
  --dry-run
```  

"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import yaml

from core.tts_preprocessor import PreprocessConfig, preprocess_for_tts
from providers.cosyvoice_tts import CosyVoiceTTS


MODEL_MATRIX = [
    {"provider": "cosyvoice", "model": "cosyvoice-v3.5-flash", "executable": True},
    {"provider": "cosyvoice", "model": "cosyvoice-v3.5-plus", "executable": True},
    {"provider": "minimax", "model": "speech-2.8-hd", "executable": False},
    {"provider": "cartesia", "model": "sonic-3.5", "executable": False},
    {"provider": "elevenlabs", "model": "eleven_multilingual_v2", "executable": False},
    {"provider": "fish_audio", "model": "s2-pro", "executable": False},
]


def provider_config(config: dict, provider: str) -> dict:
    if provider == "cosyvoice":
        return config.get("cosyvoice", {})
    return config.get("tts_model_compare", {}).get(provider, {})


def has_api_credentials(config: dict, provider: str) -> bool:
    cfg = provider_config(config, provider)
    if provider == "cosyvoice":
        return bool(str(cfg.get("api_key", "")).strip())
    return bool(str(cfg.get("api_key", "")).strip())


def read_cases(path: str) -> list[dict]:
    p = Path(path)
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [
                {"id": str(item.get("id", i + 1)), "text": item["text"]}
                for i, item in enumerate(data)
            ]
    texts = [line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [{"id": str(i + 1), "text": text} for i, text in enumerate(texts)]


def build_preprocess_config(config: dict) -> PreprocessConfig:
    tts_pre_cfg = config.get("tts_preprocess", {})
    return PreprocessConfig(
        max_sentence_chars=tts_pre_cfg.get("max_sentence_chars", 80),
        custom_word_map=tts_pre_cfg.get("custom_word_map"),
        strip_speaker_labels=tts_pre_cfg.get("strip_speaker_labels", True),
        clean_markup=tts_pre_cfg.get("clean_markup", True),
        conservative_for_risky=tts_pre_cfg.get("conservative_for_risky", True),
        risky_max_sentence_chars=tts_pre_cfg.get("risky_max_sentence_chars", 45),
    )


def synthesize_cosyvoice(config: dict, model: str, text: str, output_path: str, voice_url: str) -> str:
    cfg = dict(config)
    cfg["cosyvoice"] = dict(config.get("cosyvoice", {}))
    cfg["cosyvoice"]["model"] = model
    tts = CosyVoiceTTS(cfg)
    tts.synthesize(text=text, output_path=output_path, voice_url=voice_url or None)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or prepare TTS model A/B comparison.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--cases", required=True, help="JSON list [{'id','text'}] or one text case per line.")
    parser.add_argument("--voice-url", default="", help="Voiceprint OSS URL or voice_id for providers that support it.")
    parser.add_argument("--output-dir", default="output/tts_model_compare")
    parser.add_argument("--dry-run", action="store_true", help="Only write manifest and rating template.")
    args = parser.parse_args()

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    pre_cfg = build_preprocess_config(config)
    cases = read_cases(args.cases)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    for case in cases:
        cleaned = preprocess_for_tts(case["text"], pre_cfg)
        for model_info in MODEL_MATRIX:
            provider = model_info["provider"]
            model = model_info["model"]
            stem = f"{case['id']}_{provider}_{model}".replace("/", "_").replace(":", "_")
            audio_path = out_dir / f"{stem}.mp3"
            status = "pending"
            error = ""
            executable = model_info["executable"]
            configured = has_api_credentials(config, provider)
            provider_cfg = provider_config(config, provider)

            if executable and not configured:
                status = "missing_api_key"
                error = f"Missing API key for provider '{provider}' in config."
            elif executable and not args.dry_run:
                try:
                    if provider == "cosyvoice":
                        synthesize_cosyvoice(config, model, cleaned, str(audio_path), args.voice_url)
                        status = "generated"
                    else:
                        status = "not_implemented"
                except Exception as e:
                    status = "failed"
                    error = str(e)
            elif not executable:
                status = "provider_not_implemented"
            else:
                status = "dry_run"

            manifest.append({
                "case_id": case["id"],
                "provider": provider,
                "model": model,
                "executable": executable,
                "configured": configured,
                "status": status,
                "audio_path": str(audio_path) if audio_path.exists() else "",
                "config_section": "cosyvoice" if provider == "cosyvoice" else f"tts_model_compare.{provider}",
                "configured_model": provider_cfg.get("model", ""),
                "configured_voice_id": provider_cfg.get("voice_id", ""),
                "raw_text": case["text"],
                "tts_text": cleaned,
                "error": error,
                "garbled_heard": "",
                "voice_similarity_1_5": "",
                "chinese_naturalness_1_5": "",
                "tail_artifact_heard": "",
                "notes": "",
            })

    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(out_dir / "rating_template.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(manifest[0].keys()))
        writer.writeheader()
        writer.writerows(manifest)

    print(f"Cases: {len(cases)}")
    print(f"Manifest: {out_dir / 'manifest.json'}")
    print(f"Rating template: {out_dir / 'rating_template.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
