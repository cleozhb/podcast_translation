---
name: podcast-translation-skill
description: Use this skill when discovering podcast RSS feeds, listing podcast episodes, translating English podcast audio into Chinese audio, checking asynchronous translation status, or delivering translated podcast artifacts.
metadata:
  openclaw:
    requires:
      bins:
        - uv
---

# Podcast Translation Skill

Use the Python CLI through `shell`. All commands must run from the podcast translation repository and include `--json`.

Repository:

```bash
cd /home/zhanghuibin02/code/podcast_translation
```

## RSS

Find RSS feeds:

```bash
cd /home/zhanghuibin02/code/podcast_translation && uv run podcast_tool rss find --query "<podcast name or keywords>" --json
```

List episodes:

```bash
cd /home/zhanghuibin02/code/podcast_translation && uv run podcast_tool episodes list --rss-url "<rss_url>" --limit 10 --json
```

## Translation

Start translation only after explicit human approval.

Start from RSS episode:

```bash
cd /home/zhanghuibin02/code/podcast_translation && uv run podcast_tool translate start --rss-url "<rss_url>" --episode-id "<episode_id>" --target-lang "zh-CN" --voice-clone true --json
```

Start from direct audio URL:

```bash
cd /home/zhanghuibin02/code/podcast_translation && uv run podcast_tool translate start --audio-url "<audio_url>" --title "<episode title>" --target-lang "zh-CN" --voice-clone true --json
```

After start returns, record `job_id`. Never wait for the full translation in a single shell call.

## Status

Poll one translation task:

```bash
cd /home/zhanghuibin02/code/podcast_translation && uv run podcast_tool translate status --job-id "<job_id>" --json
```

List active translation tasks:

```bash
cd /home/zhanghuibin02/code/podcast_translation && uv run podcast_tool translate list --status active --json
```

Cancel a translation task:

```bash
cd /home/zhanghuibin02/code/podcast_translation && uv run podcast_tool translate cancel --job-id "<job_id>" --json
```

When a task is `completed`, deliver `audio_zh`, `shownotes_zh`, `transcript_en`, and `transcript_zh` from `artifacts`.

When a task is `failed`, report `error.code`, `error.message`, and whether `error.retryable` is true.

For scheduled polling, report only `completed`, `failed`, or `cancelled` tasks to the user. For `queued` or `running`, update project state quietly.
