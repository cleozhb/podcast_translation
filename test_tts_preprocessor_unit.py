"""
Focused assertions for TTS text preprocessing.

Run without pytest:
  uv run python -c "import test_tts_preprocessor_unit as t; \
t.test_strip_speaker_labels_removes_routing_text_only(); \
t.test_preprocess_removes_markup_urls_and_speaker_labels(); \
t.test_high_risk_detection_catches_tags_but_not_plain_chinese(); \
print('tts preprocessor assertions passed')"

Run with pytest, if installed:
  uv run python -m pytest test_tts_preprocessor_unit.py -q
"""

from core.tts_preprocessor import (
    PreprocessConfig,
    contains_high_risk_tts_chars,
    preprocess_for_tts,
    strip_speaker_labels,
)


def test_strip_speaker_labels_removes_routing_text_only():
    text = "[SPEAKER_00]: 你好，欢迎回来。\nSPEAKER_01: 我们继续聊 AI。"

    cleaned = strip_speaker_labels(text)

    assert "SPEAKER_00" not in cleaned
    assert "SPEAKER_01" not in cleaned
    assert "你好，欢迎回来" in cleaned
    assert "我们继续聊 AI" in cleaned


def test_preprocess_removes_markup_urls_and_speaker_labels():
    text = '[SPEAKER_00]: <tag>请看 https://example.com/a?b=1 —— "这个"（测试）🙂</tag>'

    cleaned = preprocess_for_tts(text, PreprocessConfig())

    assert "SPEAKER_00" not in cleaned
    assert "https://" not in cleaned
    assert "<tag>" not in cleaned
    assert "🙂" not in cleaned
    assert "请看" in cleaned
    assert "测试" in cleaned


def test_high_risk_detection_catches_tags_but_not_plain_chinese():
    assert contains_high_risk_tts_chars("[SPEAKER_00]: 你好")
    assert contains_high_risk_tts_chars("请看 <break>")
    assert not contains_high_risk_tts_chars("你好，今天我们继续聊人工智能。")
