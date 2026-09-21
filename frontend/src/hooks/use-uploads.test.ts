// @vitest-environment jsdom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useDatasetUploads } from "./use-uploads";

const api = vi.hoisted(() => ({
  uploadsChunkUpdate: vi.fn(),
  uploadsCreate: vi.fn(),
  uploadsInspectCreate: vi.fn(),
  uploadsRetrieve: vi.fn(),
}));
vi.mock("@/client", () => ({ default: { uploads: api } }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.resetAllMocks();
  api.uploadsCreate.mockImplementation(({ beginUploadRequest }) =>
    Promise.resolve({ chunkBytes: 1024, maxBytes: 2048, uploadId: beginUploadRequest.filename })
  );
  api.uploadsRetrieve.mockResolvedValue({ received: 0 });
  api.uploadsChunkUpdate.mockImplementation(({ body, offset }) =>
    Promise.resolve({ received: offset + body.size })
  );
  api.uploadsInspectCreate.mockResolvedValue({ rows: 10 });
});

afterEach(cleanup);

describe("dataset uploads", () => {
  it("appends files in selection order and removes them from the source", async () => {
    const { result } = renderHook(useDatasetUploads);
    act(() => result.current.add([new File(["a"], "one.csv"), new File(["b"], "two.jsonl")]));
    await waitFor(() =>
      expect(result.current.files.every((file) => file.status === "ready")).toBe(true)
    );
    expect(result.current.files.map((file) => [file.uploadId, file.rows])).toEqual([
      ["one.csv", 10],
      ["two.jsonl", 10],
    ]);
    act(() => result.current.remove(result.current.files[0].id));
    act(() => result.current.add([new File(["c"], "three.csv")]));
    await waitFor(() => expect(result.current.files[1].status).toBe("ready"));
    expect(result.current.files.map((file) => file.uploadId)).toEqual(["two.jsonl", "three.csv"]);
    expect(api.uploadsInspectCreate).toHaveBeenCalledWith(
      { id: "three.csv", inspectUploadRequest: { size: 1 } },
      { signal: expect.any(AbortSignal) }
    );
  });

  it("aborts removed files and ignores their late row counts", async () => {
    const inspection = deferred<{ rows: number }>();
    api.uploadsInspectCreate.mockReturnValueOnce(inspection.promise);
    const { result } = renderHook(useDatasetUploads);
    act(() => result.current.add([new File(["a"], "one.csv"), new File(["b"], "two.csv")]));
    await waitFor(() => expect(result.current.files[0].status).toBe("counting"));
    const signal = api.uploadsInspectCreate.mock.calls[0][1].signal;
    act(() => result.current.remove(result.current.files[0].id));
    expect(signal.aborted).toBe(true);
    await act(async () => inspection.resolve({ rows: 99 }));
    await waitFor(() => expect(result.current.files[0].status).toBe("ready"));
    expect(result.current.files).toHaveLength(1);
    expect(result.current.files[0].rows).toBe(10);
  });

  it("retries a failed file without losing other files", async () => {
    api.uploadsInspectCreate.mockRejectedValueOnce(new Error("Invalid JSON"));
    const { result } = renderHook(useDatasetUploads);
    act(() => result.current.add([new File(["a"], "one.json"), new File(["b"], "two.csv")]));
    await waitFor(() => expect(result.current.files[1].status).toBe("ready"));
    expect(result.current.files[0].error).toBe("Invalid JSON");
    act(() => result.current.retry(result.current.files[0]));
    await waitFor(() => expect(result.current.files[0].status).toBe("ready"));
    expect(result.current.files[0].error).toBeUndefined();
    expect(result.current.files).toHaveLength(2);
  });

  it("resets a dialog session without reviving cancelled uploads", async () => {
    const inspection = deferred<{ rows: number }>();
    api.uploadsInspectCreate.mockReturnValueOnce(inspection.promise);
    const { result } = renderHook(useDatasetUploads);
    act(() => result.current.add([new File(["a"], "old.csv"), new File(["b"], "queued.csv")]));
    await waitFor(() => expect(result.current.files[0].status).toBe("counting"));
    act(() => result.current.reset());
    act(() => result.current.add([new File(["c"], "new.csv")]));
    await waitFor(() => expect(result.current.files[0].status).toBe("ready"));
    await act(async () => inspection.resolve({ rows: 99 }));
    expect(result.current.files.map((file) => file.uploadId)).toEqual(["new.csv"]);
    expect(api.uploadsCreate).toHaveBeenCalledTimes(2);
  });
});
