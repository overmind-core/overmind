// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Identify providers without coupling assertions to vendor SVG paths.
vi.mock("@lobehub/icons/es/Ai2/components/Mono", () => ({
  default: () => <svg data-testid="logo-ai2" />,
}));
vi.mock("@lobehub/icons/es/Ai21/components/Mono", () => ({
  default: () => <svg data-testid="logo-ai21" />,
}));
vi.mock("@lobehub/icons/es/Anthropic/components/Mono", () => ({
  default: () => <svg data-testid="logo-anthropic" />,
}));
vi.mock("@lobehub/icons/es/Aws/components/Mono", () => ({
  default: () => <svg data-testid="logo-amazon" />,
}));
vi.mock("@lobehub/icons/es/Baichuan/components/Mono", () => ({
  default: () => <svg data-testid="logo-baichuan" />,
}));
vi.mock("@lobehub/icons/es/Baidu/components/Mono", () => ({
  default: () => <svg data-testid="logo-baidu" />,
}));
vi.mock("@lobehub/icons/es/ByteDance/components/Mono", () => ({
  default: () => <svg data-testid="logo-bytedance" />,
}));
vi.mock("@lobehub/icons/es/Claude/components/Mono", () => ({
  default: () => <svg data-testid="logo-claude" />,
}));
vi.mock("@lobehub/icons/es/Cohere/components/Mono", () => ({
  default: () => <svg data-testid="logo-cohere" />,
}));
vi.mock("@lobehub/icons/es/Cursor/components/Mono", () => ({
  default: () => <svg data-testid="logo-cursor" />,
}));
vi.mock("@lobehub/icons/es/DeepSeek/components/Mono", () => ({
  default: () => <svg data-testid="logo-deepseek" />,
}));
vi.mock("@lobehub/icons/es/Gemini/components/Mono", () => ({
  default: () => <svg data-testid="logo-google" />,
}));
vi.mock("@lobehub/icons/es/Inception/components/Mono", () => ({
  default: () => <svg data-testid="logo-inception" />,
}));
vi.mock("@lobehub/icons/es/Inflection/components/Mono", () => ({
  default: () => <svg data-testid="logo-inflection" />,
}));
vi.mock("@lobehub/icons/es/Kimi/components/Mono", () => ({
  default: () => <svg data-testid="logo-kimi" />,
}));
vi.mock("@lobehub/icons/es/Liquid/components/Mono", () => ({
  default: () => <svg data-testid="logo-liquid" />,
}));
vi.mock("@lobehub/icons/es/Meta/components/Mono", () => ({
  default: () => <svg data-testid="logo-meta" />,
}));
vi.mock("@lobehub/icons/es/Microsoft/components/Mono", () => ({
  default: () => <svg data-testid="logo-microsoft" />,
}));
vi.mock("@lobehub/icons/es/Minimax/components/Mono", () => ({
  default: () => <svg data-testid="logo-minimax" />,
}));
vi.mock("@lobehub/icons/es/Mistral/components/Mono", () => ({
  default: () => <svg data-testid="logo-mistral" />,
}));
vi.mock("@lobehub/icons/es/NousResearch/components/Mono", () => ({
  default: () => <svg data-testid="logo-nous" />,
}));
vi.mock("@lobehub/icons/es/Nvidia/components/Mono", () => ({
  default: () => <svg data-testid="logo-nvidia" />,
}));
vi.mock("@lobehub/icons/es/OpenAI/components/Mono", () => ({
  default: () => <svg data-testid="logo-openai" />,
}));
vi.mock("@lobehub/icons/es/OpenRouter/components/Mono", () => ({
  default: () => <svg data-testid="logo-openrouter" />,
}));
vi.mock("@lobehub/icons/es/Perplexity/components/Mono", () => ({
  default: () => <svg data-testid="logo-perplexity" />,
}));
vi.mock("@lobehub/icons/es/Qwen/components/Mono", () => ({
  default: () => <svg data-testid="logo-qwen" />,
}));
vi.mock("@lobehub/icons/es/Stepfun/components/Mono", () => ({
  default: () => <svg data-testid="logo-stepfun" />,
}));
vi.mock("@lobehub/icons/es/Tencent/components/Mono", () => ({
  default: () => <svg data-testid="logo-tencent" />,
}));
vi.mock("@lobehub/icons/es/Upstage/components/Mono", () => ({
  default: () => <svg data-testid="logo-upstage" />,
}));
vi.mock("@lobehub/icons/es/XAI/components/Mono", () => ({
  default: () => <svg data-testid="logo-xai" />,
}));
vi.mock("@lobehub/icons/es/XiaomiMiMo/components/Mono", () => ({
  default: () => <svg data-testid="logo-xiaomi" />,
}));
vi.mock("@lobehub/icons/es/Yi/components/Mono", () => ({
  default: () => <svg data-testid="logo-yi" />,
}));
vi.mock("@lobehub/icons/es/Zhipu/components/Mono", () => ({
  default: () => <svg data-testid="logo-zhipu" />,
}));

import { ModelOptionLabel } from "./model-option-label";
import { ModelProviderChip } from "./model-provider-chip";

afterEach(cleanup);

describe("ModelOptionLabel", () => {
  it("uses the same unboxed logo and name for catalog choices", () => {
    const { container } = render(
      <ModelOptionLabel model="openai/gpt-5.6-luna" name="OpenAI: GPT-5.6 Luna">
        <span>Fits estimated context</span>
      </ModelOptionLabel>
    );
    expect(screen.getByTestId("logo-openai")).toBeTruthy();
    expect(screen.getByText("GPT-5.6 Luna")).toBeTruthy();
    expect(screen.queryByText("OpenAI:")).toBeNull();
    expect(screen.getByText("Fits estimated context")).toBeTruthy();
    expect(screen.getByTitle("openai/gpt-5.6-luna")).toBeTruthy();
    expect(container.querySelector('[data-slot="badge"]')).toBeNull();
  });

  it("resolves fine-tuned models to their base provider without displaying the job id", () => {
    render(<ModelOptionLabel model="ft-750caa9f-qwen2-5-7b-instruct" />);
    expect(screen.getByTestId("logo-qwen")).toBeTruthy();
    expect(screen.getByText("Qwen2 5 7B Instruct · FT")).toBeTruthy();
    expect(screen.queryByText(/750caa9f/)).toBeNull();
  });

  it("preserves catalog casing and provides a neutral unknown-model fallback", () => {
    const { rerender } = render(<ModelOptionLabel model="google/gemma-4" name="Gemma 4 26B-A4B" />);
    expect(screen.getByTestId("logo-google")).toBeTruthy();
    expect(screen.getByText("Gemma 4 26B-A4B")).toBeTruthy();
    rerender(<ModelOptionLabel model="custom-model" />);
    expect(screen.getByText("Custom Model")).toBeTruthy();
  });
});

describe("ModelProviderChip", () => {
  it("renders the vendor logo, provider label and model name", () => {
    render(<ModelProviderChip model="Qwen/Qwen3.5-0.8B" />);
    expect(screen.getByTestId("logo-qwen")).toBeTruthy();
    expect(screen.getByText("Qwen")).toBeTruthy();
    expect(screen.getByText(/Qwen3.5/)).toBeTruthy();
    expect(screen.getByTitle("Qwen/Qwen3.5-0.8B")).toBeTruthy();
  });

  it("infers the provider from a bare model id", () => {
    render(<ModelProviderChip model="gemini-3.5-flash" />);
    expect(screen.getByTestId("logo-google")).toBeTruthy();
    expect(screen.getByText("Gemini")).toBeTruthy();
  });

  it("renders first-class logos for OpenRouter orgs like MiniMax and Zhipu", () => {
    const { rerender } = render(<ModelProviderChip model="minimax/minimax-m2.5" />);
    expect(screen.getByTestId("logo-minimax")).toBeTruthy();
    expect(screen.getByText("MiniMax")).toBeTruthy();

    rerender(<ModelProviderChip model="z-ai/glm-5.2" />);
    expect(screen.getByTestId("logo-zhipu")).toBeTruthy();
    expect(screen.getByText("Zhipu")).toBeTruthy();

    rerender(<ModelProviderChip model="moonshotai/kimi-k2" />);
    expect(screen.getByTestId("logo-kimi")).toBeTruthy();
    expect(screen.getByText("Kimi")).toBeTruthy();
  });

  it("renders a neutral chip for unknown models instead of breaking", () => {
    render(<ModelProviderChip model="totally-novel-model" />);
    expect(screen.getByText("Model")).toBeTruthy();
    expect(screen.getByText("Totally Novel Model")).toBeTruthy();
    expect(screen.queryByTestId(/logo-/)).toBeNull();
  });

  it("can hide the model id for tight layouts", () => {
    render(<ModelProviderChip model="gpt-4o" showModelId={false} />);
    expect(screen.getByText("OpenAI")).toBeTruthy();
    expect(screen.queryByText("GPT 4o")).toBeNull();
  });
});
