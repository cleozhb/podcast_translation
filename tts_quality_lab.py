"""
Offline TTS quality lab entrypoint.

Purpose:
  Analyze human-labeled TTS samples and check whether local acoustic windows
  can separate "normal" from "garbled" audio. This script is experimental and
  is not called by the production podcast translation pipeline.

Required input:
  A directory containing normal/ and/or garbled/ subdirectories. Audio files are
  required. Same-name .txt files are optional and should contain the TTS input
  text for that audio segment.

Example input:
  samples/
    normal/
      ok1.mp3
      ok1.txt        # optional
    garbled/
      bad1.mp3
      bad1.txt       # optional

Run:
  uv run python tts_quality_lab.py samples \
    --output-dir output/tts_quality_lab \
    --export-windows

Output:
  output/tts_quality_lab/tts_quality_lab_report.json
  output/tts_quality_lab/tts_quality_lab_samples.csv
  output/tts_quality_lab/tts_quality_lab_feature_separation.csv
  output/tts_quality_lab/windows/   # only when --export-windows is used
"""

from core.tts_quality_lab import main


if __name__ == "__main__":
    raise SystemExit(main())
