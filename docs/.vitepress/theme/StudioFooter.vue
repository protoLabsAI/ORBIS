<script setup lang="ts">
// Studio attribution shown at the bottom of every docs page (injected via the
// `layout-bottom` slot in theme/index.ts so it follows sidebar pages too —
// VitePress's built-in themeConfig.footer hides itself on sidebar layouts).
//
// Brand rule (protoContent foundation): the wordmark is *always* `protoLabs.studio`
// — lowercase p, capital L, the `.studio` dot is part of the mark. Public
// surfaces link to protolabs.studio.
</script>

<template>
  <footer class="studio-footer">
    <span class="built">Built by</span>
    <a class="wordmark" href="https://protolabs.studio" target="_blank" rel="noreferrer">
      protoLabs.studio
    </a>
  </footer>
</template>

<style scoped>
.studio-footer {
  border-top: 1px solid var(--vp-c-divider);
  padding: 24px 24px 28px;
  text-align: center;
  font-size: 13px;
  color: var(--vp-c-text-2);
  display: flex;
  gap: 0.4em;
  align-items: baseline;
  justify-content: center;
}

.wordmark {
  font-weight: 600;
  /* protoLabs brand gradient: lavender → indigo. */
  background: var(--pl-gradient-brand, linear-gradient(135deg, #9b87f2 0%, #818cf8 50%, #6366f1 100%));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  color: transparent;
  transition: opacity 0.15s ease;
}

.wordmark:hover {
  opacity: 0.8;
}
</style>
