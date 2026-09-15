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
async function uploadFile(
  file: File,
  onProgress: (p: UploadProgress) => void,
  signal?: AbortSignal
): Promise<string> {
  let reserved: UploadReserved;
  try {
    reserved = await apiClient.uploads.uploadsCreate({
      beginUploadRequest: { filename: file.name },
    });
  } catch (err) {
    throw await uploadError(err);
  }
  if (file.size > reserved.maxBytes) {
    throw new Error(
      `${file.name} is ${gb(file.size)} — files are capped at ${gb(reserved.maxBytes)}.`
    );
  }
  let sent = (await apiClient.uploads.uploadsRetrieve({ id: reserved.uploadId })).received;
  onProgress({ filename: file.name, sent, total: file.size });
  while (sent < file.size) {
    if (signal?.aborted) throw new DOMException("Upload cancelled", "AbortError");
    const end = Math.min(sent + reserved.chunkBytes, file.size);
    let state: UploadState;
    try {
      state = await apiClient.uploads.uploadsChunkUpdate(
        { body: file.slice(sent, end), id: reserved.uploadId, offset: sent },
        { signal }
      );
    } catch (err) {
      throw await uploadError(err);
    }
    sent = state.received;
    onProgress({ filename: file.name, sent, total: file.size });
  }
  return reserved.uploadId;
}

export function useFileUpload() {
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const start = useCallback(async (file: File): Promise<string | null> => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    setProgress({ filename: file.name, sent: 0, total: file.size });
    try {
      return await uploadFile(file, setProgress, controller.signal);
    } catch (err) {
      if ((err as Error)?.name === "AbortError") return null;
      setProgress(null);
      setError((err as Error)?.message || "Upload failed.");
      return null;
    }
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setProgress(null);
  }, []);

  const reset = useCallback(() => {
    setProgress(null);
    setError(null);
  }, []);

  return { cancel, error, progress, reset, start };
}
