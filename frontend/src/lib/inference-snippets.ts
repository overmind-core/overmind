import { config } from "@/config";

export function inferenceBaseUrl(apiUrl = config.apiUrl): string {
  return `${apiUrl.replace(/\/$/, "")}/api/v1`;
}

export function buildCurlSnippet({
  baseUrl,
  modelId,
}: {
  baseUrl: string;
  modelId: string;
}): string {
  return [
    `curl ${baseUrl}/chat/completions \\`,
    `  -H "Authorization: Bearer $OVERMIND_API_KEY" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{`,
    `    "model": "${modelId}",`,
    `    "messages": [{"role": "user", "content": "Hello"}],`,
    `    "max_tokens": 256`,
    `  }'`,
  ].join("\n");
}

export function buildPythonSnippet({
  baseUrl,
  modelId,
}: {
  baseUrl: string;
  modelId: string;
}): string {
  return [
    `import os`,
    `from openai import OpenAI`,
    ``,
    `client = OpenAI(`,
    `    base_url="${baseUrl}",`,
    `    api_key=os.environ["OVERMIND_API_KEY"],`,
    `)`,
    ``,
    `resp = client.chat.completions.create(`,
    `    model="${modelId}",`,
    `    messages=[{"role": "user", "content": "Hello"}],`,
    `    max_tokens=256,`,
    `)`,
    `print(resp.choices[0].message.content)`,
  ].join("\n");
}

export function buildDecisionCurlSnippet({
  apiUrl = config.apiUrl,
  jobId,
}: {
  apiUrl?: string;
  jobId: string;
}): string {
  return [
    `curl ${apiUrl.replace(/\/$/, "")}/api/decision-jobs/${jobId}/systemone/ \\`,
    `  -H "Authorization: Bearer $OVERMIND_API_KEY" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{`,
    `    "state": "Ticket: I was charged twice for order 1002.",`,
    `    "questions": {"route": {"type": "choice", "instructions": "Which team handles this?",`,
    `      "criteria": {"billing": "Billing", "shipping": "Shipping"}}}`,
    `  }'`,
  ].join("\n");
}
