/// <reference types="astro/client" />

// vite-plugin-glsl imports shader assets as source strings.
declare module '*.glsl' {
  const source: string;
  export default source;
}
