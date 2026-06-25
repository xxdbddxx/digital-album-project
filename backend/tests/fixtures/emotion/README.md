# Emotion Fixtures

This directory contains audio fixtures for testing and evaluating the multimodal user emotion enhancement system.

Due to privacy and size constraints, audio files (`*.wav`), manifests (`*.jsonl`), and evaluation reports (`*.json`) within this directory are intentionally ignored by Git.

## Requirements

A complete evaluation set should contain:

- 8 classes × 20 samples = 160 samples minimum.
- Represented subsets:
  - Natural speech
  - Acted speech
  - Conflicting modalities (e.g. sad tone, happy words)
  - Explicit commands
  - Background noise
  - Weak/ambiguous signals
