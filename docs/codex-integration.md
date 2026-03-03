# Codex Integration Setup

This document outlines how to add OpenAI Codex (Codex Spark) to the CI pipeline,
mirroring the existing Claude Code integration. Codex will be able to respond to
`@codex` mentions in issues and PRs, and can optionally perform automated code reviews.

## Required GitHub Secrets

Add the following secret in **Settings → Secrets and variables → Actions → New repository secret**:

| Secret Name     | Description                                  |
|----------------|----------------------------------------------|
| `OPENAI_API_KEY` | Your OpenAI API key with Codex access        |

## Workflow Files to Create

Because GitHub Apps cannot self-modify workflow files, create these files manually.

### 1. `.github/workflows/codex.yml` — Interactive (tag `@codex`)

Mirrors `claude.yml`. Triggers when someone mentions `@codex` in a comment, issue, or review.

```yaml
name: Codex

on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]
  issues:
    types: [opened, assigned]
  pull_request_review:
    types: [submitted]

jobs:
  codex:
    if: |
      github.event.sender.type != 'Bot' &&
      (
        (github.event_name == 'issue_comment' && contains(github.event.comment.body, '@codex')) ||
        (github.event_name == 'pull_request_review_comment' && contains(github.event.comment.body, '@codex')) ||
        (github.event_name == 'pull_request_review' && contains(github.event.review.body, '@codex')) ||
        (github.event_name == 'issues' && (contains(github.event.issue.body, '@codex') || contains(github.event.issue.title, '@codex')))
      )
    runs-on: ubuntu-latest
    timeout-minutes: 60
    permissions:
      contents: write
      pull-requests: write
      issues: write
      id-token: write
      actions: read
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 1

      - name: Run Codex
        uses: openai/codex-action@v1
        with:
          api_key: ${{ secrets.OPENAI_API_KEY }}
```

> **Note:** Verify the action name `openai/codex-action@v1` against the
> [GitHub Marketplace](https://github.com/marketplace) — OpenAI may publish it under
> a different name or version. Update `api_key` parameter name to match the action's docs.

### 2. `.github/workflows/codex-code-review.yml` — Automatic PR Code Review

Mirrors `claude-code-review.yml`. Runs on every non-draft PR open/update.

```yaml
name: Codex Code Review

on:
  pull_request:
    types: [opened, synchronize, ready_for_review, reopened]
    # Optional: restrict to specific paths
    # paths:
    #   - "overmind/**/*.py"
    #   - "tests/**/*.py"

concurrency:
  group: codex-review-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  codex-review:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    permissions:
      contents: read
      pull-requests: write
      issues: read
      id-token: write

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 1

      - name: Run Codex Code Review
        uses: openai/codex-action@v1
        with:
          api_key: ${{ secrets.OPENAI_API_KEY }}
          mode: review
```

> **Note:** Confirm the `mode: review` parameter with Codex Spark's action documentation.
> If the action doesn't support a `mode` flag, check for an alternative plugin/prompt mechanism.

## Manual Steps Checklist

Complete these steps in order after the PR is merged:

### Step 1 — Get an OpenAI API Key
1. Go to [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
2. Create a new key with access to Codex / GPT-4 class models
3. Copy the key — you won't be able to view it again

### Step 2 — Add the Secret to This Repository
1. Go to **Settings → Secrets and variables → Actions**
2. Click **New repository secret**
3. Name: `OPENAI_API_KEY`, Value: the key from Step 1
4. Click **Add secret**

### Step 3 — Install the Codex GitHub App (if applicable)
If Codex Spark provides a GitHub App (similar to how Bugbot works):
1. Visit the Codex Spark listing on [GitHub Marketplace](https://github.com/marketplace)
2. Click **Install** / **Set up a plan**
3. Grant access to the `overmind-core/overmind` repository
4. Follow any OAuth / API key linking flow the app requires

### Step 4 — Create the Workflow Files
1. Create `.github/workflows/codex.yml` with the YAML from **Section 1** above
2. Create `.github/workflows/codex-code-review.yml` with the YAML from **Section 2** above
3. Commit and push to `main` (or open a separate PR)

### Step 5 — Test the Integration
1. Open a test PR or issue
2. Comment `@codex please summarize this PR` to verify the interactive workflow fires
3. Check the **Actions** tab to confirm both workflows run without errors

## Permissions Reference

The workflows above require these GitHub token permissions (already included in the YAML):

| Workflow                  | `contents` | `pull-requests` | `issues` | `actions` |
|---------------------------|-----------|-----------------|----------|-----------|
| `codex.yml`               | write     | write           | write    | read      |
| `codex-code-review.yml`   | read      | write           | read     | —         |

`contents: write` is needed so Codex can create branches and commit changes when asked
to implement fixes (same as the Claude workflow). If you only want read-only reviews,
downgrade it to `read`.
