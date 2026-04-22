// Ambient declarations for vite-plugin-glsl: every .glsl / .vert / .frag
// import resolves to a string containing the compiled shader source.

declare module '*.glsl' {
  const src: string;
  export default src;
}
declare module '*.vert' {
  const src: string;
  export default src;
}
declare module '*.frag' {
  const src: string;
  export default src;
}
declare module '*.vs' {
  const src: string;
  export default src;
}
declare module '*.fs' {
  const src: string;
  export default src;
}
