import { afterEach, expect, test } from "bun:test";
import { context, trace } from "@opentelemetry/api";

import { flush, init, shutdown, startActiveObservation } from "../src";

afterEach(async () => {
  await shutdown();
  trace.disable();
  context.disable();
});

test("spans reach {baseUrl}/api/v1/traces as protobuf with the API key", async () => {
  const requests: Array<{ path: string; apiKey: string | null; contentType: string | null; body: Uint8Array }> = [];
  const server = Bun.serve({
    port: 0,
    async fetch(req) {
      requests.push({
        path: new URL(req.url).pathname,
        apiKey: req.headers.get("x-api-key"),
        contentType: req.headers.get("content-type"),
        body: new Uint8Array(await req.arrayBuffer()),
      });
      return new Response(new Uint8Array(), { status: 200, headers: { "content-type": "application/x-protobuf" } });
    },
  });

  try {
    init({
      apiKey: "ovr_test_key",
      baseUrl: `http://localhost:${server.port}/`,
      exportMode: "immediate",
      capabilityId: "cap-wire",
    });
    await startActiveObservation("wire-check", async (span) => {
      span.update({ input: "ping", output: "pong" });
    });
    await flush();
  } finally {
    await server.stop(true);
  }

  expect(requests).toHaveLength(1);
  const [request] = requests;
  expect(request?.path).toBe("/api/v1/traces");
  expect(request?.apiKey).toBe("ovr_test_key");
  expect(request?.contentType).toBe("application/x-protobuf");
  const text = new TextDecoder("latin1").decode(request?.body);
  for (const needle of ["wire-check", "overmind.span.type", "cap-wire", "outputs"]) expect(text).toContain(needle);
  expect(text).not.toContain("langfuse.observation");
});
