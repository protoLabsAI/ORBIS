---
layout: home
hero:
  name: protoVoice
  text: Full-duplex voice agent
  tagline: Whisper STT + vLLM + Fish Audio / Kokoro TTS on Pipecat — sub-200ms TTFA, speak-while-thinking, push-interrupt.
  actions:
    - theme: brand
      text: Get Started
      link: /tutorials/
    - theme: alt
      text: Reference
      link: /reference/

features:
  - icon: 🎤
    title: Streaming Pipeline
    details: Mic → Silero VAD → Whisper Turbo → Qwen → Fish / Kokoro → Speaker. Each stage streams so audio plays while the LLM is still generating.
  - icon: 🔁
    title: Duplex Turn-Taking
    details: The agent speaks filler while tools run, and interrupts the user to deliver long-running tool results. No polite waiting.
  - icon: 🐟
    title: Pluggable TTS
    details: Fish Audio S2-Pro (default, voice-cloning, 44.1 kHz) or Kokoro 82M (low-latency preset voices) — swap via env.
  - icon: 🔌
    title: A2A-Ready
    details: Dispatches to other protoLabs agents via A2A, starting with Ava as the orchestrator. Optional inbound A2A endpoint so other agents can call us.
---

## Documentation Structure

This site follows the [Diátaxis](https://diataxis.fr) framework:

| Section | Purpose | Start here if you… |
|---------|---------|---------------------|
| [**Tutorials**](/tutorials/) | Learning-oriented walkthroughs | Are new to protoVoice |
| [**How-To Guides**](/guides/) | Task-oriented procedures | Need to accomplish something specific |
| [**Reference**](/reference/) | Technical descriptions | Need exact details on an API, env var, or frame type |
| [**Explanation**](/explanation/) | Understanding-oriented discussion | Want to understand how and why things work |
