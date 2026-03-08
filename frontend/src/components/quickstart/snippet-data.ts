export type Language = "python" | "javascript";
export type Vendor = "openai" | "anthropic" | "gemini" | "agno";

export interface VendorConfig {
  id: Vendor;
  label: string;
  comingSoon?: boolean;
}

export interface LanguageConfig {
  id: Language;
  label: string;
  vendors: VendorConfig[];
}

export interface SnippetData {
  installCommand: string;
  codeSnippet: (apiKey: string) => string;
}

export const LANGUAGES: LanguageConfig[] = [
  {
    id: "python",
    label: "Python",
    vendors: [
      { id: "openai", label: "OpenAI" },
      { id: "anthropic", label: "Anthropic" },
      { id: "gemini", label: "Google Gemini" },
      { id: "agno", label: "Agno", comingSoon: true },
    ],
  },
  {
    id: "javascript",
    label: "JavaScript / TypeScript",
    vendors: [
      { id: "openai", label: "OpenAI" },
      { id: "anthropic", label: "Anthropic" },
      { id: "gemini", label: "Google Gemini" },
      { id: "agno", label: "Agno", comingSoon: true },
    ],
  },
];

const PYTHON_SNIPPETS: Record<Vendor, SnippetData> = {
  openai: {
    installCommand: "pip install overmind-sdk openai",
    codeSnippet: (apiKey) => `from overmind_sdk import init
from openai import OpenAI

init(
    overmind_api_key="${apiKey}",
    service_name="my-service",
)

client = OpenAI()

response = client.chat.completions.create(
    model="gpt-5-mini",
    messages=[{"role": "user", "content": "Explain quantum computing"}],
)
print(response.choices[0].message.content)`,
  },
  anthropic: {
    installCommand: "pip install overmind-sdk anthropic",
    codeSnippet: (apiKey) => `from overmind_sdk import init
import anthropic

init(
    overmind_api_key="${apiKey}",
    service_name="my-service",
)

client = anthropic.Anthropic()

message = client.messages.create(
    model="claude-opus-4-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Explain quantum computing"}],
)
print(message.content[0].text)`,
  },
  gemini: {
    installCommand: "pip install overmind-sdk google-genai",
    codeSnippet: (apiKey) => `from overmind_sdk import init
from google import genai

init(
    overmind_api_key="${apiKey}",
    service_name="my-service",
)

client = genai.Client()

response = client.models.generate_content(
    model="gemini-2.0-flash",
    contents="Explain quantum computing",
)
print(response.text)`,
  },
  agno: {
    installCommand: "pip install overmind-sdk agno openai",
    codeSnippet: (apiKey) => `from overmind_sdk import init
from agno.agent import Agent
from agno.models.openai import OpenAIChat

init(
    overmind_api_key="${apiKey}",
    service_name="my-service",
)

agent = Agent(
    model=OpenAIChat(id="gpt-5-mini"),
    instructions="You are a helpful assistant.",
    markdown=True,
)
agent.print_response("Explain quantum computing")`,
  },
};

const JS_SNIPPETS: Partial<Record<Vendor, SnippetData>> = {
  openai: {
    installCommand: "npm install @overmind-lab/trace-sdk openai",
    codeSnippet: (apiKey) => `import { OpenAI } from "openai";
import { OvermindClient } from "@overmind-lab/trace-sdk";

const overmindClient = new OvermindClient({
  apiKey: "${apiKey}",
  appName: "my app",
});

overmindClient.initTracing({
  enableBatching: false,
  enabledProviders: { openai: OpenAI },
  instrumentations: [],
});

const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

const response = await openai.chat.completions.create({
  model: "gpt-5-mini",
  messages: [{ role: "user", content: "Explain quantum computing" }],
});

await overmindClient.shutdown();`,
  },
  anthropic: {
    installCommand: "npm install @overmind-lab/trace-sdk @anthropic-ai/sdk",
    codeSnippet: (apiKey) => `import * as Anthropic from "@anthropic-ai/sdk";
import { OvermindClient } from "@overmind-lab/trace-sdk";

const overmindClient = new OvermindClient({
  apiKey: "${apiKey}",
  appName: "my app",
});

overmindClient.initTracing({
  enableBatching: false,
  enabledProviders: { anthropic: Anthropic },
  instrumentations: [],
});

const client = new Anthropic.default({ apiKey: process.env.ANTHROPIC_API_KEY });

const message = await client.messages.create({
  model: "claude-sonnet-4-20250514",
  max_tokens: 1024,
  messages: [{ role: "user", content: "Explain quantum computing" }],
});

console.log(message.content[0].text);

await overmindClient.shutdown();`,
  },
  gemini: {
    installCommand: "npm install @overmind-lab/trace-sdk @google/genai",
    codeSnippet: (apiKey) => `import * as GoogleGenAI from "@google/genai";
import { OvermindClient } from "@overmind-lab/trace-sdk";

const overmindClient = new OvermindClient({
  apiKey: "${apiKey}",
  appName: "my app",
});

overmindClient.initTracing({
  enableBatching: false,
  enabledProviders: { googleGenAI: GoogleGenAI },
  instrumentations: [],
});

const client = new GoogleGenAI.GoogleGenAI({
  apiKey: process.env.GEMINI_API_KEY,
});

const response = await client.models.generateContent({
  model: "gemini-2.0-flash",
  contents: "Explain quantum computing",
});

console.log(response.text);

await overmindClient.shutdown();`,
  },
};

export function getSnippet(language: Language, vendor: Vendor): SnippetData | null {
  if (language === "python") return PYTHON_SNIPPETS[vendor] ?? null;
  if (language === "javascript") return JS_SNIPPETS[vendor] ?? null;
  return null;
}
