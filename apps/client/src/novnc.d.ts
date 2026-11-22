// noVNC 1.7 exports the client from the package root; @types/novnc__novnc still declares it
// under its former path.
declare module "@novnc/novnc" {
  export { default } from "@novnc/novnc/lib/rfb";
}
