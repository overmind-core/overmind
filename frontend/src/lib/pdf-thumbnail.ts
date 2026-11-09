import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

GlobalWorkerOptions.workerSrc = workerUrl;

export async function pdfThumbnail(file: File, signal: AbortSignal): Promise<Blob> {
  const loading = getDocument({
    data: new Uint8Array(await file.arrayBuffer()),
    useSystemFonts: true,
  });
  const cancel = () => void loading.destroy();
  signal.addEventListener("abort", cancel, { once: true });
  try {
    signal.throwIfAborted();
    const pdf = await loading.promise;
    const page = await pdf.getPage(1);
    const original = page.getViewport({ scale: 1 });
    const viewport = page.getViewport({ scale: 240 / Math.max(original.width, original.height) });
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    await page.render({ canvas, viewport }).promise;
    signal.throwIfAborted();
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("Preview unavailable"))))
    );
  } finally {
    signal.removeEventListener("abort", cancel);
    await loading.destroy();
  }
}
