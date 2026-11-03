# PDF.js 6.4.299

Bundled from the official `pdfjs-dist` npm package (Apache-2.0; see LICENSE).
Only the legacy core renderer and worker are used. CMaps, standard fonts and
image-decoding WebAssembly assets are included for offline PDF rendering.
The scripting runtime and the full viewer/component bundles are omitted.

`static/js/pdf_preview.js` renders visible pages to canvases for a simple,
scrollable PDF preview. No text-selection layer or viewer controls are included.

Source: https://github.com/mozilla/pdf.js
Distribution: https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-6.4.299.tgz
