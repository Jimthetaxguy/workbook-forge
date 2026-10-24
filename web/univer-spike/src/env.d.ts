/// <reference types="vite/client" />
declare module '*.xlsx?inline' {
  const dataUrl: string;
  export default dataUrl;
}
