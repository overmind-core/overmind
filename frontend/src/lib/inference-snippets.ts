import { config } from "@/config";
import type { DecisionInputRequest } from "@/openapi";

export function inferenceBaseUrl(apiUrl = config.apiUrl): string {
  return `${apiUrl.replace(/\/$/, "")}/api/v1`;
}

function sdkBaseUrl(apiUrl = config.apiUrl): string {
  return inferenceBaseUrl(apiUrl).replace(/\/v1$/, "");
}

function choiceCriteria(decision: DecisionInputRequest): Record<string, string | null> {
  const described = new Map<string, string>();
  for (const line of decision.question.split("\n").slice(1)) {
    const split = line.indexOf(": ");
    if (split <= 0) continue;
    const name = line.slice(0, split);
    const description = line.slice(split + 2).trim();
    if (decision.options.includes(name) && description) described.set(name, description);
  }
  return Object.fromEntries(
    decision.options.map((option) => [option, described.get(option) ?? null])
  );
}

function decisionQuestion(decision: DecisionInputRequest): string {
  const instructions = JSON.stringify(decision.question);
  if (decision.kind === "noul") {
    return [
      '    "decision": Noul(',
      `        instructions=${instructions},`,
      `        criteria={"false": ${JSON.stringify(decision.options[0] ?? "false")}, "true": ${JSON.stringify(decision.options[1] ?? "true")}},`,
      "    ),",
    ].join("\n");
  }
  if (decision.kind === "score") {
    const levels = decision.options
      .map((option) => `        ${JSON.stringify(option)},`)
      .join("\n");
    return [
      '    "decision": Score(',
      `        instructions=${instructions},`,
      "        criteria=[",
      levels,
      "        ],",
      "    ),",
    ].join("\n");
  }
  const criteria = Object.entries(choiceCriteria(decision))
    .map(
      ([option, description]) =>
        `        ${JSON.stringify(option)}: ${description == null ? "None" : JSON.stringify(description)},`
    )
    .join("\n");
  return [
    '    "decision": Choice(',
    `        instructions=${instructions},`,
    "        criteria={",
    criteria,
    "        },",
    "    ),",
  ].join("\n");
}

function decisionClass(kind: string): string {
  if (kind === "noul") return "Noul";
  if (kind === "score") return "Score";
  return "Choice";
}

export function buildDecisionSnippets(
  modelId: string,
  decision: DecisionInputRequest,
  apiUrl = config.apiUrl
) {
  const endpoint = `${inferenceBaseUrl(apiUrl)}/systemone`;
  const criteria =
    decision.kind === "noul"
      ? {
          false: decision.options[0] ?? "false",
          true: decision.options[1] ?? "true",
        }
      : decision.kind === "score"
        ? decision.options
        : choiceCriteria(decision);
  const body = JSON.stringify(
    {
      model: modelId,
      questions: {
        decision: {
          criteria,
          instructions: decision.question,
          type: decision.kind,
        },
      },
      state: decision.state,
    },
    null,
    2
  );
  const shellQuote = (value: string) => `'${value.replaceAll("'", "'\"'\"'")}'`;
  return {
    curl: [
      "# pip install typesafe-sdk  —  or send the same body with curl.",
      "# A cold start can take a few minutes. The call waits for the answer.",
      `curl --fail-with-body --silent --show-error --max-time 200 ${shellQuote(endpoint)} \\`,
      '  -H "Authorization: Bearer $OVERMIND_API_KEY" \\',
      '  -H "Content-Type: application/json" \\',
      `  --data ${shellQuote(body)}`,
    ].join("\n"),
    python: [
      "import os",
      `from typesafe_sdk import ${decisionClass(decision.kind)}, TypeSafeClient`,
      "",
      "# Example from this model's training version. Replace state with your input.",
      "with TypeSafeClient(",
      '    api_key=os.environ["OVERMIND_API_KEY"],',
      `    base_url=${JSON.stringify(sdkBaseUrl(apiUrl))},`,
      `    model=${JSON.stringify(modelId)},`,
      "    timeout=200,",
      "    compression=False,",
      ") as client:",
      "    response = client.system_one(",
      `        state=${JSON.stringify(decision.state)},`,
      "        questions={",
      decisionQuestion(decision),
      "        },",
      "    )",
      decision.kind === "noul"
        ? 'print(response.nouls["decision"].noul)'
        : decision.kind === "score"
          ? 'print(response.scores["decision"].score)\nprint(response.scores["decision"].probabilities)'
          : 'print(response.choices["decision"].choice)\nprint(response.choices["decision"].probabilities)',
    ].join("\n"),
  };
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
