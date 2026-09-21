import { useCallback, useEffect, useRef, useState } from "react";

import apiClient from "@/client";
import type { UploadReserved, UploadState } from "@/openapi";
import { ResponseError } from "@/openapi";

export interface UploadProgress {
  sent: number;
  total: number;
  filename: string;
}

/** The server's refusal reason, when there is one — never a generic code. */
async function uploadError(err: unknown): Promise<Error> {
  if (err instanceof ResponseError) {
    const detail = (await err.response.json().catch(() => null))?.detail;
    if (typeof detail === "string" && detail) return new Error(detail);
  }
  return err instanceof Error ? err : new Error("Upload failed.");
}

const gb = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(1)} GB`;

/** Send a file in chunks, resuming from whatever the server already holds. */
const CHUNK_ATTEMPTS = 4;

/** The server stores a chunk once however often it arrives, so a dropped
 *  connection is answered by sending the same bytes again. A refusal is final. */
async function sendChunk(
  body: Blob,
  id: string,
  offset: number,
  signal?: AbortSignal
): Promise<UploadState> {
  for (let attempt = 1; ; attempt++) {
    try {
      return await apiClient.uploads.uploadsChunkUpdate({ body, id, offset }, { signal });
    } catch (err) {
      const refused = err instanceof ResponseError;
      if (refused || signal?.aborted || attempt === CHUNK_ATTEMPTS) throw await uploadError(err);
      await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** attempt));
    }
  }
}

async function uploadFile(
  file: File,
  onProgress: (p: UploadProgress) => void,
  signal?: AbortSignal
): Promise<string> {
  let reserved: UploadReserved;
  try {
    reserved = await apiClient.uploads.uploadsCreate(
      {
        beginUploadRequest: { filename: file.name },
      },
      { signal }
    );
  } catch (err) {
    throw await uploadError(err);
  }
  if (file.size > reserved.maxBytes) {
    throw new Error(
      `${file.name} is ${gb(file.size)} — files are capped at ${gb(reserved.maxBytes)}.`
    );
  }
  let sent = (await apiClient.uploads.uploadsRetrieve({ id: reserved.uploadId }, { signal }))
    .received;
  onProgress({ filename: file.name, sent, total: file.size });
  while (sent < file.size) {
    if (signal?.aborted) throw new DOMException("Upload cancelled", "AbortError");
    const end = Math.min(sent + reserved.chunkBytes, file.size);
    sent = (await sendChunk(file.slice(sent, end), reserved.uploadId, sent, signal)).received;
    onProgress({ filename: file.name, sent, total: file.size });
  }
  return reserved.uploadId;
}

export interface DatasetUpload {
  id: string;
  file: File;
  status: "queued" | "uploading" | "counting" | "ready" | "error";
  percent: number;
  uploadId?: string;
  rows?: number;
  error?: string;
}

export function useDatasetUploads() {
  const [files, setFiles] = useState<DatasetUpload[]>([]);
  const controllers = useRef(new Map<string, AbortController>());
  const queue = useRef(Promise.resolve());

  const reset = useCallback(() => {
    for (const controller of controllers.current.values()) controller.abort();
    controllers.current.clear();
    queue.current = Promise.resolve();
    setFiles([]);
  }, []);

  useEffect(() => reset, [reset]);

  const enqueue = useCallback((entry: DatasetUpload) => {
    const controller = new AbortController();
    controllers.current.set(entry.id, controller);
    const update = (patch: Partial<DatasetUpload>) => {
      if (!controller.signal.aborted) {
        setFiles((previous) =>
          previous.map((file) => (file.id === entry.id ? { ...file, ...patch } : file))
        );
      }
    };
    queue.current = queue.current.then(async () => {
      if (controller.signal.aborted) return;
      try {
        update({ status: "uploading" });
        const uploadId = await uploadFile(
          entry.file,
          (progress) => {
            update({ percent: Math.round((progress.sent / Math.max(progress.total, 1)) * 100) });
          },
          controller.signal
        );
        if (controller.signal.aborted) return;
        update({ status: "counting" });
        const inspected = await apiClient.uploads.uploadsInspectCreate(
          {
            id: uploadId,
            inspectUploadRequest: { size: entry.file.size },
          },
          { signal: controller.signal }
        );
        update({ rows: inspected.rows, status: "ready", uploadId });
      } catch (err) {
        if (!controller.signal.aborted) {
          const error = await uploadError(err);
          update({ error: error.message, status: "error" });
        }
      } finally {
        if (controllers.current.get(entry.id) === controller) controllers.current.delete(entry.id);
      }
    });
  }, []);

  const add = useCallback(
    (incoming: File[]) => {
      const entries: DatasetUpload[] = incoming.map((file) => ({
        file,
        id: crypto.randomUUID(),
        percent: 0,
        status: "queued",
      }));
      setFiles((previous) => [...previous, ...entries]);
      for (const entry of entries) enqueue(entry);
    },
    [enqueue]
  );

  const remove = useCallback((id: string) => {
    controllers.current.get(id)?.abort();
    controllers.current.delete(id);
    setFiles((previous) => previous.filter((file) => file.id !== id));
  }, []);

  const retry = useCallback(
    (entry: DatasetUpload) => {
      const next: DatasetUpload = { ...entry, error: undefined, percent: 0, status: "queued" };
      setFiles((previous) => previous.map((file) => (file.id === entry.id ? next : file)));
      enqueue(next);
    },
    [enqueue]
  );

  return { add, files, remove, reset, retry };
}
