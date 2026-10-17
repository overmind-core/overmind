import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { context, trace } from "@opentelemetry/api";
import { InMemorySpanExporter, type ReadableSpan } from "@opentelemetry/sdk-trace-base";

import {
  flush,
  init,
  observe,
  propagateAttributes,
  shutdown,
  startActiveObservation,
  startObservation,
  withCapability,
} from "../src";

let memory: InMemorySpanExporter;

beforeEach(() => {
  memory = new InMemorySpanExporter();
  init({ exporter: memory, exportMode: "immediate", capabilityId: "cap-default", capabilityName: "Support" });
});

afterEach(async () => {
  await shutdown();
  trace.disable();
  context.disable();
});

function exported(name: string): ReadableSpan {
  const span = memory.getFinishedSpans().find((s) => s.name === name);
  if (!span) throw new Error(`no exported span named ${name}`);
  return span;
}

describe("langfuse observations exported in Overmind's vocabulary", () => {
  test("generation carries model, tokens, cost and request parameters", async () => {
    await startActiveObservation(
      "answer",
      async (generation) => {
        generation.update({
          model: "openai/gpt-5-mini",
          modelParameters: { temperature: 0.2, max_tokens: 512, seed: 7 },
          input: [{ role: "user", content: "hi" }],
          output: "hello",
          usageDetails: { input: 11, output: 4, cache_read_input_tokens: 3 },
          costDetails: { input: 0.001, output: 0.002 },
        });
      },
      { asType: "generation" },
    );
    await flush();

    const attrs = exported("answer").attributes;
    expect(attrs).toMatchObject({
      "overmind.span.type": "llm_call",
      "genai.model": "openai/gpt-5-mini",
      "gen_ai.request.model": "openai/gpt-5-mini",
      "genai.request.temperature": 0.2,
      "genai.request.max_tokens": 512,
      "genai.request.kwargs": '{"seed":7}',
      "genai.prompt_tokens": 11,
      "genai.completion_tokens": 4,
      "genai.total_tokens": 15,
      "genai.cache_read_tokens": 3,
      "gen_ai.usage.prompt_tokens": 11,
      "gen_ai.usage.total_tokens": 15,
      "overmind.status": "success",
      inputs: '[{"role":"user","content":"hi"}]',
      outputs: "hello",
      "overmind.capability.id": "cap-default",
    });
    expect(attrs["genai.cost"]).toBeCloseTo(0.003);
    expect(Object.keys(attrs).filter((k) => k.startsWith("langfuse."))).toEqual([]);
  });

  test("observation types map to span types, tool and retrieval facts", async () => {
    await startActiveObservation("planner", async () => {
      startObservation("lookup", { input: { orderId: "o1", verbose: true }, output: { ok: true } }, { asType: "tool" }).end();
      startObservation("search", { input: "refund policy", output: [1, 2, 3] }, { asType: "retriever" }).end();
    }, { asType: "agent" });
    await flush();

    expect(exported("planner").attributes["overmind.span.type"]).toBe("workflow");
    expect(exported("lookup").attributes).toMatchObject({
      "overmind.span.type": "tool_call",
      "tool.name": "lookup",
      "tool.arg_keys": '["orderId","verbose"]',
    });
    expect(exported("search").attributes).toMatchObject({
      "overmind.span.type": "retrieval",
      "overmind.retrieval.query_chars": 13,
      "overmind.retrieval.result_count": 3,
    });
  });

  test("a throwing observed function is reported failed with its message", async () => {
    const boom = observe(
      async function boom() {
        throw new TypeError("bad input");
      },
      { name: "boom" },
    );
    await expect(boom()).rejects.toThrow("bad input");
    await flush();

    expect(exported("boom").attributes).toMatchObject({
      "overmind.status": "failed",
      "overmind.error.message": "bad input",
      "overmind.span.type": "function",
    });
  });

  test("session, trace name, metadata and capability scope follow the active context", async () => {
    await withCapability({ id: "cap-refunds", name: "Refunds" }, () =>
      propagateAttributes({ sessionId: "sess-1", traceName: "refund-flow", metadata: { tenant: "acme" } }, () =>
        startActiveObservation("step", async () => {}),
      ),
    );
    await flush();

    expect(exported("step").attributes).toMatchObject({
      "overmind.capability.id": "cap-refunds",
      "overmind.capability.name": "Refunds",
      "conversation.id": "sess-1",
      "overmind.workflow.name": "refund-flow",
      "metadata.tenant": "acme",
    });
  });

  test("observe declarations land on the span", async () => {
    const handle = observe(async (message: string) => `re: ${message}`, {
      name: "handle",
      capabilityId: "cap-triage",
      intent: "Triage a ticket",
      expectations: ["Cites the order id", "  "],
      referenceTrajectory: ["lookup_order"],
    });
    expect(await handle("hi")).toBe("re: hi");
    await flush();

    expect(exported("handle").attributes).toMatchObject({
      "overmind.capability.id": "cap-triage",
      "overmind.intent": "Triage a ticket",
      "overmind.expectations": ["Cites the order id"],
      "overmind.reference_trajectory": ["lookup_order"],
      inputs: "hi",
      outputs: "re: hi",
    });
    expect(exported("handle").attributes).not.toHaveProperty("overmind.capability.name");
  });

  test("spans unrelated to observability are not exported", async () => {
    trace.getTracer("http").startSpan("GET /health").end();
    trace.getTracer("http").startSpan("custom", { attributes: { "gen_ai.operation.name": "chat" } }).end();
    await flush();

    expect(memory.getFinishedSpans().map((s) => s.name)).toEqual(["custom"]);
  });
});
