// Render scrollable PDF pages using the bundled PDF.js core.
const ASSETS = new URL('../vendor/pdfjs/', import.meta.url).href;

export function mountPdfPreview(container, url, name) {
  let disposed = false;
  let loading, pdf, observer, resizing;
  let painting = false;
  const pages = [];
  const root = document.createElement('div');
  root.className = 'file-preview-pdf';
  root.setAttribute('role', 'region');
  root.setAttribute('aria-label', name || 'PDF preview');
  const status = document.createElement('div');
  status.className = 'pdf-preview-status';
  status.setAttribute('role', 'status');
  status.textContent = 'Loading PDF…';
  root.appendChild(status);
  container.appendChild(root);

  function release(item) {
    item.task?.cancel();
    if (item.canvas) {
      item.canvas.width = item.canvas.height = 0;
      item.canvas.remove();
      item.canvas = null;
    }
  }

  async function paint() {
    if (painting || disposed) return;
    painting = true;
    try {
      let item;
      while (!disposed && (item = pages.find(p => p.visible && !p.canvas && !p.failed))) {
        let page;
        try {
          page = await pdf.getPage(item.number);
          if (disposed || !item.visible) continue;
          const base = page.getViewport({ scale: 1 });
          const width = item.sheet.clientWidth;
          if (!width) break;
          const viewport = page.getViewport({ scale: width / base.width });
          item.sheet.style.aspectRatio = `${base.width} / ${base.height}`;
          item.width = width;
          const density = Math.min(devicePixelRatio || 1, 2,
            Math.sqrt(2000000 / (viewport.width * viewport.height)));
          const canvas = document.createElement('canvas');
          canvas.width = Math.ceil(viewport.width * density);
          canvas.height = Math.ceil(viewport.height * density);
          canvas.setAttribute('aria-hidden', 'true');
          item.canvas = canvas;
          item.sheet.appendChild(canvas);
          item.task = page.render({ canvasContext: canvas.getContext('2d'), viewport,
            transform: [density, 0, 0, density, 0, 0] });
          await item.task.promise;
        } catch (error) {
          if (!disposed && !['RenderingCancelledException', 'AbortException'].includes(error.name)) {
            item.failed = true;
            item.sheet.textContent = `Could not render page ${item.number}. Use Download to open it.`;
          }
        } finally {
          item.task = null;
          page?.cleanup();
        }
      }
    } finally {
      painting = false;
    }
  }

  async function load() {
    try {
      const pdfjs = await import('../vendor/pdfjs/pdf.min.mjs');
      if (disposed) return;
      pdfjs.GlobalWorkerOptions.workerSrc = `${ASSETS}pdf.worker.min.mjs`;
      loading = pdfjs.getDocument({ url, disableAutoFetch: true, disableStream: true,
        rangeChunkSize: 262144, cMapUrl: `${ASSETS}cmaps/`, cMapPacked: true,
        standardFontDataUrl: `${ASSETS}standard_fonts/`, wasmUrl: `${ASSETS}wasm/`,
        isEvalSupported: false });
      pdf = await loading.promise;
      if (disposed) return;
      const first = await pdf.getPage(1);
      const viewport = first.getViewport({ scale: 1 });
      first.cleanup();
      if (disposed) return;
      for (let number = 1; number <= pdf.numPages; number++) {
        const sheet = document.createElement('div');
        sheet.className = 'pdf-preview-sheet';
        sheet.style.aspectRatio = `${viewport.width} / ${viewport.height}`;
        sheet.setAttribute('role', 'group');
        sheet.setAttribute('aria-label', `Page ${number} of ${pdf.numPages}`);
        pages.push({ number, sheet });
        root.appendChild(sheet);
      }
      status.remove();
      const byElement = new Map(pages.map(item => [item.sheet, item]));
      observer = new IntersectionObserver(entries => {
        for (const entry of entries) {
          const item = byElement.get(entry.target);
          item.visible = entry.isIntersecting;
          if (!item.visible) release(item);
        }
        paint();
      }, { root: container, rootMargin: '400px 0px' });
      pages.forEach(item => observer.observe(item.sheet));
      resizing = new ResizeObserver(() => {
        for (const item of pages) {
          if (item.width && item.width !== item.sheet.clientWidth) release(item);
        }
        paint();
      });
      resizing.observe(root);
    } catch (error) {
      if (disposed) return;
      status.setAttribute('role', 'alert');
      status.textContent = error.name === 'PasswordException'
        ? 'This PDF needs a password. Use Download to open it.'
        : 'Could not load this PDF. Use Download to open it.';
    }
  }

  load();
  return () => {
    disposed = true;
    observer?.disconnect();
    resizing?.disconnect();
    pages.forEach(release);
    loading?.destroy().catch(() => {});
    root.remove();
  };
}
