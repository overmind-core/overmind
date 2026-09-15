import { describe, expect, it } from "vitest";

import { getModelProviderInfo } from "./model-provider";

describe("getModelProviderInfo", () => {
  it("maps bare model ids to their provider", () => {
    expect(getModelProviderInfo("gpt-4o").id).toBe("openai");
    expect(getModelProviderInfo("o3-mini").id).toBe("openai");
    expect(getModelProviderInfo("claude-sonnet-4-5").id).toBe("anthropic");
    expect(getModelProviderInfo("gemini-3.5-flash").id).toBe("google");
    expect(getModelProviderInfo("gemma-2-9b").id).toBe("google");
    expect(getModelProviderInfo("llama-3.3-70b").id).toBe("meta");
    expect(getModelProviderInfo("mistral-large").id).toBe("mistral");
    expect(getModelProviderInfo("mixtral-8x7b").id).toBe("mistral");
    expect(getModelProviderInfo("qwen3.5-0.8b").id).toBe("qwen");
    expect(getModelProviderInfo("grok-4").id).toBe("xai");
    expect(getModelProviderInfo("command-r").id).toBe("cohere");
    expect(getModelProviderInfo("glm-5.2").id).toBe("zhipu");
    expect(getModelProviderInfo("minimax-m2").id).toBe("minimax");
    expect(getModelProviderInfo("kimi-k2").id).toBe("kimi");
    expect(getModelProviderInfo("sonar-pro").id).toBe("perplexity");
  });

  it("resolves OpenRouter-style prefixes for major orgs", () => {
    expect(getModelProviderInfo("Qwen/Qwen3.5-0.8B").id).toBe("qwen");
    expect(getModelProviderInfo("anthropic/claude-sonnet-4-5").id).toBe("anthropic");
    expect(getModelProviderInfo("google/gemini-2.5-pro").id).toBe("google");
    expect(getModelProviderInfo("meta-llama/Llama-3.3-70B-Instruct").id).toBe("meta");
    expect(getModelProviderInfo("unsloth/Muse-Glimmer-30B").id).toBe("meta");
    expect(getModelProviderInfo("unsloth/gemma-4-E2B-it").id).toBe("google");
    expect(getModelProviderInfo("unsloth/gpt-oss-20b-BF16").id).toBe("openai");
    expect(getModelProviderInfo("mistralai/Mistral-Small").id).toBe("mistral");
    expect(getModelProviderInfo("deepseek/deepseek-chat-v3-0324").id).toBe("deepseek");
    expect(getModelProviderInfo("deepseek-ai/DeepSeek-V3.1").id).toBe("deepseek");
    expect(getModelProviderInfo("nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16").id).toBe("nvidia");
    expect(getModelProviderInfo("nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B").id).toBe("nvidia");
    expect(getModelProviderInfo("unsloth/NVIDIA-Nemotron-3.5-Lightning-30B-A3B").id).toBe("nvidia");
    expect(getModelProviderInfo("microsoft/Phi-4-mini-instruct").id).toBe("microsoft");
    expect(getModelProviderInfo("moonshotai/kimi-k2").id).toBe("kimi");
    expect(getModelProviderInfo("x-ai/grok-4.5").id).toBe("xai");
    expect(getModelProviderInfo("allenai/Olmo-3-7B-Instruct").id).toBe("ai2");
    expect(getModelProviderInfo("minimax/minimax-m2.5").id).toBe("minimax");
    expect(getModelProviderInfo("z-ai/glm-5.2").id).toBe("zhipu");
    expect(getModelProviderInfo("perplexity/sonar-pro").id).toBe("perplexity");
    expect(getModelProviderInfo("amazon/nova-pro-v1").id).toBe("amazon");
    expect(getModelProviderInfo("bytedance-seed/seed-2.0-lite").id).toBe("bytedance");
    expect(getModelProviderInfo("nousresearch/hermes-4-70b").id).toBe("nous");
    expect(getModelProviderInfo("tencent/hunyuan-a13b-instruct").id).toBe("tencent");
    expect(getModelProviderInfo("stepfun/step-3.5-flash").id).toBe("stepfun");
    expect(getModelProviderInfo("xiaomi/mimo-v2.5").id).toBe("xiaomi");
    expect(getModelProviderInfo("inception/mercury-2").id).toBe("inception");
    expect(getModelProviderInfo("upstage/solar-pro-3").id).toBe("upstage");
    expect(getModelProviderInfo("ai21/jamba-large-1.7").id).toBe("ai21");
    expect(getModelProviderInfo("baidu/ernie-4.5-vl-424b-a47b").id).toBe("baidu");
    expect(getModelProviderInfo("inflection/inflection-3-pi").id).toBe("inflection");
    expect(getModelProviderInfo("openrouter/auto-beta").id).toBe("openrouter");
    expect(getModelProviderInfo("~anthropic/claude-sonnet-4-5").id).toBe("anthropic");
  });

  it("keeps the model label readable for prefixed ids", () => {
    const info = getModelProviderInfo("Qwen/Qwen3.5-0.8B");
    expect(info.providerLabel).toBe("Qwen");
    expect(info.modelLabel).toContain("Qwen3.5");
  });

  it("renders model labels in vendor casing, not all caps", () => {
    const label = (model: string) => getModelProviderInfo(model).modelLabel;
    expect(label("gpt-4o")).toBe("GPT 4o");
    expect(label("gpt-4o-mini")).toBe("GPT 4o Mini");
    expect(label("gemini-2.5-pro")).toBe("Gemini 2.5 Pro");
    expect(label("mixtral-8x7b")).toBe("Mixtral 8x7B");
    expect(label("phi-4-mini-instruct")).toBe("Phi 4 Mini Instruct");
    expect(label("o3-mini")).toBe("o3 Mini");
  });

  it("keeps size units and initialisms in caps", () => {
    const label = (model: string) => getModelProviderInfo(model).modelLabel;
    expect(label("llama-3.1-8b")).toBe("Llama 3.1 8B");
    expect(label("claude-opus-4")).toBe("Claude Opus 4");
    expect(label("command-r")).toBe("Command R");
  });

  it("falls back to a neutral unknown provider instead of breaking", () => {
    const bare = getModelProviderInfo("totally-novel-model");
    expect(bare.id).toBe("unknown");
    expect(bare.providerLabel).toBe("Model");
    expect(bare.modelLabel).not.toBe("");

    const prefixed = getModelProviderInfo("fdtn-ai/antares-350m");
    expect(prefixed.id).toBe("unknown");
    expect(prefixed.providerLabel).toBe("Cisco");
    expect(prefixed.providerSlug).toBe("cisco");
    expect(prefixed.modelLabel).toContain("Antares");
  });
});
