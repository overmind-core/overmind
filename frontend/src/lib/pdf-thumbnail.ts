import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

const WIDTH = 384;

/** The first page of a PDF as an image URL the caller must revoke. pdf.js loads on first
 *  use, so it stays out of the initial bundle. */
export async function pdfThumbnail(file: File): Promise<string> {
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
  const task = pdfjs.getDocument({ data: await file.arrayBuffer() });
  try {
    const page = await (await task.promise).getPage(1);
    const viewport = page.getViewport({ scale: 1 });
    const scaled = page.getViewport({ scale: WIDTH / viewport.width });
    const canvas = document.createElement("canvas");
    canvas.width = scaled.width;
    canvas.height = scaled.height;
    await page.render({ canvas, viewport: scaled }).promise;
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
    if (!blob) throw new Error("The page could not be drawn.");
    return URL.createObjectURL(blob);
  } finally {
    void task.destroy();
  }
}
