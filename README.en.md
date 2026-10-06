# BiliScribe-Audio

[![Offline checks](https://github.com/826784562/BiliScribe-Audio/actions/workflows/ci.yml/badge.svg)](https://github.com/826784562/BiliScribe-Audio/actions/workflows/ci.yml)

[中文](README.md) · [Quick start](docs/quickstart.md) · [Examples](examples/README.md)

Turn Bilibili subtitles and speech transcripts into source-traceable study notes.

This is a Codex Skill with local preparation tools. The **Codex model executing the skill** understands the evidence and writes the notes. Python does not call the running conversation model or independently produce a course. Audio focuses on transcript-only understanding and does not inspect video frames.

## Install

Python 3.11+ (3.12 recommended), Git and Codex are required.

```bash
git clone https://github.com/826784562/BiliScribe-Audio.git
cd BiliScribe-Audio
python install.py
```

The installer copies a self-contained skill to CODEX_HOME/skills (default ~/.codex/skills). It does not download dependencies or models. It stops if the destination exists; --update explicitly updates source files and preserves local .env/.venv. Open a new Codex conversation and ask:

> Use $bili-scribe-audio. Inspect and set up the local dependencies. Confirm the lessons in this course: <URL>. Understand the subtitles or transcript only, then combine my slides and selected textbook chapters into notes. Preserve citations and label missing evidence and independent AI explanations. 

Or open this repository in Codex and use the project skill under .agents/skills/bili-scribe-audio. See the bilingual commands in [quick start](docs/quickstart.md). Local ASR is optional: add --with-asr when setting up dependencies.

## Outputs and evidence

Lesson notes, a course outline, glossary, separate exercise explanations and a delivery report are stored under outputs/<course-id>/. Stable lesson identities and understanding records retain source references. Textbooks require chapter selection and a confirmed printed/PDF page mapping. Course speech, slides, textbook supplements and AI explanations remain distinct.

Both editions understand spoken content through subtitles/transcripts, **not direct raw-audio input**. Codex processing follows the account's own service and billing arrangement. Optional cloud ASR/OCR uploads only the selected material after user authorization; local ASR may download model weights on first use. Media, real transcripts, textbooks, caches and secrets are excluded from distribution.

## Limits and verification

Image-only slides, handwritten formulas and screen-only parameters are outside Audio’s scope; use BiliScribe-Vision when these matter. Automatic validation checks identities, citations, coverage and stale inputs; it cannot certify semantic accuracy. Examples are original synthetic scenes, not real course benchmarks. Network/ASR compatibility still needs live, authorized testing.

## Contribute

See [CONTRIBUTING](CONTRIBUTING.md), [security guidance](SECURITY.md) and [roadmap](docs/roadmap.md). Use anonymized, redistributable reproductions. CI is configured for Windows and Ubuntu on Python 3.11/3.12; all four platform/version jobs have passed; the badge shows the current status.

GPL-3.0-or-later. Original copyright and third-party attribution are preserved in [LICENSE](LICENSE) and [NOTICE](NOTICE). Preserves the v11 audio-focused product scope with later timestamp and provenance fixes backported. See the companion [BiliScribe-Vision](https://github.com/826784562/BiliScribe-Vision).
