import { config } from "@/config";
import type { DecisionInputRequest } from "@/openapi";

export function inferenceBaseUrl(apiUrl = config.apiUrl): string {
  return `${apiUrl.replace(/\/$/, "")}/api/v1`;
}

export function buildDecisionSnippets(
  deploymentId: string,
  decision: DecisionInputRequest,
  apiUrl = config.apiUrl
) {
  const base = `${apiUrl.replace(/\/$/, "")}/api`;
  const endpoint = `${base}/deployed-models/${deploymentId}/inference/`;
  const shellQuote = (value: string) => `'${value.replaceAll("'", "'\"'\"'")}'`;
  const decisions = JSON.stringify([decision], null, 2);
  return {
    curl: [
      "# Requires curl, jq and uuidgen. Keep request_key when retrying this request.",
      'request_key="$(uuidgen)"',
      `decisions=${shellQuote(decisions)}`,
      'payload=$(jq -n --arg key "$request_key" --argjson decisions "$decisions" \\',
      "  '{request_key: $key, decisions: $decisions}')",
      "",
      `job=$(curl --fail-with-body --silent --show-error --max-time 30 ${shellQuote(endpoint)} \\`,
      '  -H "Authorization: Bearer $OVERMIND_API_KEY" \\',
      '  -H "Content-Type: application/json" \\',
      '  --data "$payload") || exit 1',
      `request_url=${shellQuote(`${base}/inference-requests/`)}$(printf '%s' "$job" | jq -er '.id')/`,
      "",
      "for attempt in $(seq 1 150); do",
      `  request_status=$(printf '%s' "$job" | jq -er '.status') || exit 1`,
      '  case "$request_status" in',
      "    succeeded) break ;;",
      `    failed|unresolved) printf '%s\\n' "$job" >&2; exit 1 ;;`,
      "  esac",
      "  sleep 2",
      '  job=$(curl --fail-with-body --silent --show-error --max-time 30 "$request_url" \\',
      '    -H "Authorization: Bearer $OVERMIND_API_KEY") || exit 1',
      "done",
      `printf '%s' "$job" | jq -e '.status == "succeeded"' >/dev/null || {`,
      '  echo "Still pending: poll $request_url; do not resubmit." >&2; exit 1;',
      "}",
      `printf '%s' "$job" | jq '.result.predictions[] | . as $p |`,
      "  (.probabilities | index(max)) as $i |",
      "  {label: $p.options[$i], probabilities: $p.probabilities}'",
    ].join("\n"),
    python: [
      "import os",
      "import time",
      "import uuid",
      "import requests",
      "",
      "# Example input from this model's pinned training version. Replace state for your input.",
      `decisions = ${decisions}`,
      'headers = {"Authorization": "Bearer " + os.environ["OVERMIND_API_KEY"]}',
      "request_key = str(uuid.uuid4())  # Keep this key if retrying this request.",
      `response = requests.post(${JSON.stringify(endpoint)}, headers=headers, json={`,
      '    "request_key": request_key,',
      '    "decisions": decisions,',
      "}, timeout=30)",
      "response.raise_for_status()",
      "job = response.json()",
      `request_url = ${JSON.stringify(`${base}/inference-requests/`)} + job["id"] + "/"`,
      "deadline = time.monotonic() + 300",
      'while job["status"] not in {"succeeded", "failed", "unresolved"}:',
      "    if time.monotonic() >= deadline:",
      '        raise TimeoutError(f"Still pending: poll {request_url}; do not resubmit.")',
      "    time.sleep(2)",
      "    response = requests.get(request_url, headers=headers, timeout=30)",
      "    response.raise_for_status()",
      "    job = response.json()",
      'if job["status"] != "succeeded":',
      "    raise RuntimeError(job)",
      'for prediction in job["result"]["predictions"]:',
      '    probabilities = prediction["probabilities"]',
      "    index = max(range(len(probabilities)), key=probabilities.__getitem__)",
      '    print({"label": prediction["options"][index], "probabilities": probabilities})',
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
