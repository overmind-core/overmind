// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// The @lobehub icon ESM subpaths don't resolve under vitest's node resolver
// (they only resolve through Vite); stub each one the chip imports. The
// factories are inlined because vi.mock calls are hoisted above any variable.
vi.mock("@lobehub/icons/es/Ai2", () => ({ default: () => <svg data-testid="logo-ai2" /> }));
vi.mock("@lobehub/icons/es/Ai21", () => ({ default: () => <svg data-testid="logo-ai21" /> }));
vi.mock("@lobehub/icons/es/Anthropic", () => ({
  default: () => <svg data-testid="logo-anthropic" />,
}));
vi.mock("@lobehub/icons/es/Aws", () => ({ default: () => <svg data-testid="logo-amazon" /> }));
vi.mock("@lobehub/icons/es/Baichuan", () => ({
  default: () => <svg data-testid="logo-baichuan" />,
}));
vi.mock("@lobehub/icons/es/Baidu", () => ({ default: () => <svg data-testid="logo-baidu" /> }));
vi.mock("@lobehub/icons/es/ByteDance", () => ({
  default: () => <svg data-testid="logo-bytedance" />,
}));
vi.mock("@lobehub/icons/es/Claude", () => ({ default: () => <svg data-testid="logo-claude" /> }));
vi.mock("@lobehub/icons/es/Cohere", () => ({ default: () => <svg data-testid="logo-cohere" /> }));
vi.mock("@lobehub/icons/es/Cursor", () => ({ default: () => <svg data-testid="logo-cursor" /> }));
vi.mock("@lobehub/icons/es/DeepSeek", () => ({
  default: () => <svg data-testid="logo-deepseek" />,
}));
vi.mock("@lobehub/icons/es/Gemini", () => ({ default: () => <svg data-testid="logo-google" /> }));
vi.mock("@lobehub/icons/es/Inception", () => ({
  default: () => <svg data-testid="logo-inception" />,
}));
vi.mock("@lobehub/icons/es/Inflection", () => ({
  default: () => <svg data-testid="logo-inflection" />,
}));
vi.mock("@lobehub/icons/es/Kimi", () => ({ default: () => <svg data-testid="logo-kimi" /> }));
vi.mock("@lobehub/icons/es/Liquid", () => ({ default: () => <svg data-testid="logo-liquid" /> }));
vi.mock("@lobehub/icons/es/Meta", () => ({ default: () => <svg data-testid="logo-meta" /> }));
vi.mock("@lobehub/icons/es/Microsoft", () => ({
  default: () => <svg data-testid="logo-microsoft" />,
}));
vi.mock("@lobehub/icons/es/Minimax", () => ({ default: () => <svg data-testid="logo-minimax" /> }));
vi.mock("@lobehub/icons/es/Mistral", () => ({ default: () => <svg data-testid="logo-mistral" /> }));
vi.mock("@lobehub/icons/es/NousResearch", () => ({
  default: () => <svg data-testid="logo-nous" />,
}));
vi.mock("@lobehub/icons/es/Nvidia", () => ({ default: () => <svg data-testid="logo-nvidia" /> }));
vi.mock("@lobehub/icons/es/OpenAI", () => ({ default: () => <svg data-testid="logo-openai" /> }));
vi.mock("@lobehub/icons/es/OpenRouter", () => ({
  default: () => <svg data-testid="logo-openrouter" />,
}));
vi.mock("@lobehub/icons/es/Perplexity", () => ({
  default: () => <svg data-testid="logo-perplexity" />,
}));
vi.mock("@lobehub/icons/es/Qwen", () => ({ default: () => <svg data-testid="logo-qwen" /> }));
vi.mock("@lobehub/icons/es/Stepfun", () => ({ default: () => <svg data-testid="logo-stepfun" /> }));
vi.mock("@lobehub/icons/es/Tencent", () => ({ default: () => <svg data-testid="logo-tencent" /> }));
vi.mock("@lobehub/icons/es/Upstage", () => ({ default: () => <svg data-testid="logo-upstage" /> }));
vi.mock("@lobehub/icons/es/XAI", () => ({ default: () => <svg data-testid="logo-xai" /> }));
vi.mock("@lobehub/icons/es/XiaomiMiMo", () => ({
  default: () => <svg data-testid="logo-xiaomi" />,
}));
vi.mock("@lobehub/icons/es/Yi", () => ({ default: () => <svg data-testid="logo-yi" /> }));
vi.mock("@lobehub/icons/es/Zhipu", () => ({ default: () => <svg data-testid="logo-zhipu" /> }));

import { ModelProviderChip } from "./model-provider-chip";

afterEach(cleanup);

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
