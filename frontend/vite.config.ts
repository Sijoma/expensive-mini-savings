import { defineConfig } from "vite";

// One minified ES module, served by the integration from custom_components/ems/frontend/.
export default defineConfig({
  build: {
    outDir: "../custom_components/ems/frontend",
    emptyOutDir: true,
    target: "es2022",
    minify: "esbuild",
    modulePreload: false,
    copyPublicDir: false,
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      input: "src/ems-panel.ts",
      preserveEntrySignatures: "strict",
      output: { format: "es", entryFileNames: "ems-panel.js", inlineDynamicImports: true },
    },
  },
});
