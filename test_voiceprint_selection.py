"""
Focused assertions for voiceprint candidate selection.

This test synthesizes a local tone and verifies that the voiceprint selector
prefers a candidate after skip_initial_seconds.

Run without pytest:
  uv run python -c "import test_voiceprint_selection as t; \
t.test_voiceprint_candidate_skips_initial_region(); \
print('voiceprint selection assertions passed')"

Run with pytest, if installed:
  uv run python -m pytest test_voiceprint_selection.py -q
"""

from pydub import AudioSegment
from pydub.generators import Sine

from core.audio_utils import SpeakerSegment, _select_voiceprint_candidate


def test_voiceprint_candidate_skips_initial_region():
    tone = Sine(440).to_audio_segment(duration=140_000).apply_gain(-20)
    segments = [
        SpeakerSegment("SPEAKER_00", 5.0, 35.0),
        SpeakerSegment("SPEAKER_00", 100.0, 135.0),
    ]

    start, end, score = _select_voiceprint_candidate(
        audio=tone,
        segments=segments,
        target_duration=20.0,
        min_segment_duration=5.0,
        skip_initial_seconds=90.0,
    )

    assert start >= 90.0
    assert end > start
    assert score > -1.0
