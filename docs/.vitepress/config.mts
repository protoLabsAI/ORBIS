import { defineConfig } from "vitepress";

export default defineConfig({
  title: "protoVoice",
  description:
    "Full-duplex voice agent on Pipecat — Whisper STT + vLLM + Fish Audio / Kokoro TTS",
  base: "/protoVoice/",

  head: [["link", { rel: "icon", href: "/protoVoice/favicon.svg" }]],

  themeConfig: {
    logo: "/favicon.svg",

    nav: [
      { text: "Tutorials", link: "/tutorials/" },
      { text: "Guides", link: "/guides/" },
      { text: "Reference", link: "/reference/" },
      { text: "Explanation", link: "/explanation/" },
    ],

    sidebar: {
      "/tutorials/": [
        {
          text: "Tutorials",
          items: [
            { text: "Overview", link: "/tutorials/" },
            { text: "First Voice Session", link: "/tutorials/first-voice-session" },
            { text: "Running with Docker Compose", link: "/tutorials/docker-compose" },
          ],
        },
      ],

      "/guides/": [
        {
          text: "How-To Guides",
          items: [
            { text: "Overview", link: "/guides/" },
            { text: "Switch TTS Backend", link: "/guides/switch-tts-backend" },
            { text: "Clone a Voice", link: "/guides/clone-a-voice" },
            { text: "Use an External LLM", link: "/guides/external-llm" },
            { text: "Use LocalAI (all-API)", link: "/guides/use-localai" },
            { text: "Configure Verbosity", link: "/guides/verbosity" },
            { text: "Backchannels", link: "/guides/backchannels" },
            { text: "Delivery Policies", link: "/guides/delivery-policies" },
            { text: "Personas & Skills", link: "/guides/personas-and-skills" },
            { text: "Users & API Keys", link: "/guides/users" },
            { text: "Audio Handling (echo / turn)", link: "/guides/audio-handling" },
            { text: "Build a Tool", link: "/guides/build-tools" },
            { text: "A2A Integration", link: "/guides/a2a-integration" },
            { text: "Benchmarking", link: "/guides/benchmarking" },
            { text: "Run Without the Fish Sidecar", link: "/guides/no-fish" },
          ],
        },
      ],

      "/reference/": [
        {
          text: "Reference",
          items: [
            { text: "Overview", link: "/reference/" },
            { text: "Environment Variables", link: "/reference/environment-variables" },
            { text: "HTTP API", link: "/reference/http-api" },
            { text: "WebRTC Protocol", link: "/reference/webrtc-protocol" },
            { text: "TTS Backends", link: "/reference/tts-backends" },
            { text: "Pipeline Shape", link: "/reference/pipeline-shape" },
            { text: "Orb Visualizer", link: "/reference/orb-visualizer" },
            { text: "Tools", link: "/reference/tools" },
            { text: "Delegates", link: "/reference/delegates" },
            { text: "Memory", link: "/reference/memory" },
            { text: "Tracing", link: "/reference/tracing" },
            { text: "Tracing Contract", link: "/reference/tracing-contract" },
            { text: "Metrics", link: "/reference/metrics" },
          ],
        },
      ],

      "/explanation/": [
        {
          text: "Explanation",
          items: [
            { text: "Overview", link: "/explanation/" },
            { text: "Architecture", link: "/explanation/architecture" },
            { text: "Duplex Design", link: "/explanation/duplex-design" },
            { text: "Natural-Sounding Fillers", link: "/explanation/natural-fillers" },
            { text: "Why Pipecat", link: "/explanation/why-pipecat" },
            { text: "Two-Model Split", link: "/explanation/two-model-split" },
            { text: "Browser-First Inference (deferred)", link: "/explanation/browser-first-inference" },
          ],
        },
      ],
    },

    socialLinks: [
      { icon: "github", link: "https://github.com/protoLabsAI/protoVoice" },
    ],

    search: {
      provider: "local",
    },

    footer: {
      message: "Part of the protoLabs autonomous development studio.",
      copyright: "© 2026 protoLabs.studio",
    },
  },
});
