import { useState } from "react";

import { Check, Copy, ExternalLink, Loader2, Terminal } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { type Language, LANGUAGES, type Vendor, getSnippet } from "./snippet-data";
import { useQuickstartKey } from "./use-quickstart-key";

const DOCS_URL = "https://docs.overmindlab.ai/guides/getting-started/";

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <Button
      aria-label="Copy to clipboard"
      className="absolute top-2 right-2 size-7 opacity-0 transition-opacity group-hover/code:opacity-100"
      onClick={handleCopy}
      size="icon"
      variant="ghost"
    >
      {copied ? <Check className="size-3.5 text-green-500" /> : <Copy className="size-3.5" />}
    </Button>
  );
}

function CodeBlock({ children, label }: { children: string; label?: string }) {
  return (
    <div className="group/code relative">
      {label && (
        <div className="flex items-center gap-1.5 rounded-t-md border border-b-0 border-border bg-muted/80 px-3 py-1.5">
          <Terminal className="size-3.5 text-muted-foreground" />
          <span className="text-xs font-medium text-muted-foreground">{label}</span>
        </div>
      )}
      <pre
        className={`overflow-x-auto border border-border bg-zinc-950 p-4 font-mono text-sm leading-relaxed text-zinc-100 ${label ? "rounded-b-md" : "rounded-md"}`}
      >
        {children}
      </pre>
      <CopyButton text={children} />
    </div>
  );
}

function ComingSoonPlaceholder({ vendor }: { vendor: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-md border border-dashed border-border py-12">
      <Badge className="mb-3" variant="secondary">
        Coming Soon
      </Badge>
      <p className="text-sm text-muted-foreground">
        {vendor} support for JavaScript/TypeScript is in development.
      </p>
      <a
        className="mt-2 inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
        href={DOCS_URL}
        rel="noopener noreferrer"
        target="_blank"
      >
        Check the docs for updates <ExternalLink className="size-3.5" />
      </a>
    </div>
  );
}

function SnippetPanel({
  apiKey,
  language,
  vendor,
}: {
  apiKey: string;
  language: Language;
  vendor: Vendor;
}) {
  const vendorConfig = LANGUAGES.find((l) => l.id === language)?.vendors.find(
    (v) => v.id === vendor
  );

  if (vendorConfig?.comingSoon) {
    return <ComingSoonPlaceholder vendor={vendorConfig.label} />;
  }

  const snippet = getSnippet(language, vendor);
  if (!snippet) return null;

  return (
    <div className="space-y-4">
      <div>
        <p className="mb-2 text-sm font-medium text-foreground">1. Install the SDK</p>
        <CodeBlock label="Terminal">{snippet.installCommand}</CodeBlock>
      </div>
      <div>
        <p className="mb-2 text-sm font-medium text-foreground">
          2. Replace your import and add your API key
        </p>
        <CodeBlock>{snippet.codeSnippet(apiKey)}</CodeBlock>
      </div>
      <div className="rounded-md border border-border bg-muted/30 px-4 py-3">
        <p className="text-sm font-medium text-foreground">3. Send at least 10 traces</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Run your application normally. Once Overmind collects 10+ traces, it automatically
          extracts prompt templates, creates Agents, and starts optimizing.
        </p>
      </div>
    </div>
  );
}

export function QuickstartSnippets({ compact = false }: { compact?: boolean }) {
  const { apiKey, isLoading, isError, retry } = useQuickstartKey();
  const [language, setLanguage] = useState<Language>("python");
  const [vendor, setVendor] = useState<Vendor>("openai");

  const currentLanguage = LANGUAGES.find((l) => l.id === language)!;

  const handleLanguageChange = (value: string) => {
    setLanguage(value as Language);
    setVendor("openai");
  };

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <Loader2 className="mb-3 size-6 animate-spin text-muted-foreground" />
        <p className="text-sm text-muted-foreground">Preparing your API key...</p>
      </div>
    );
  }

  if (isError || !apiKey) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <p className="mb-3 text-sm text-destructive">Failed to generate API key.</p>
        <Button onClick={retry} size="sm" variant="outline">
          Retry
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {!compact && (
        <div>
          <h2 className="font-display text-2xl font-bold">Connect your LLM application</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Add Overmind to your project in 2 minutes. Pick your language and provider, then copy
            the code.
          </p>
        </div>
      )}

      <div className="rounded-lg border border-border bg-card">
        <div className="space-y-1 border-b border-border p-4">
          <div className="flex items-center justify-between">
            <Tabs onValueChange={handleLanguageChange} value={language}>
              <TabsList>
                {LANGUAGES.map((lang) => (
                  <TabsTrigger key={lang.id} value={lang.id}>
                    {lang.label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            <a
              className="hidden items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground sm:inline-flex"
              href={DOCS_URL}
              rel="noopener noreferrer"
              target="_blank"
            >
              Full docs <ExternalLink className="size-3.5" />
            </a>
          </div>

          <Tabs onValueChange={(v) => setVendor(v as Vendor)} value={vendor}>
            <TabsList className="h-8 bg-transparent p-0">
              {currentLanguage.vendors.map((v) => (
                <TabsTrigger
                  className="h-7 gap-1.5 text-xs data-[state=active]:bg-muted"
                  key={v.id}
                  value={v.id}
                >
                  {v.label}
                  {v.comingSoon && (
                    <Badge className="px-1 py-0 text-[10px] leading-tight" variant="secondary">
                      Soon
                    </Badge>
                  )}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>

        <div className="p-4">
          <SnippetPanel apiKey={apiKey} language={language} vendor={vendor} />
        </div>
      </div>

      <div className="flex items-center gap-3">
        <a
          className="inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline"
          href={DOCS_URL}
          rel="noopener noreferrer"
          target="_blank"
        >
          Getting Started Guide <ExternalLink className="size-3.5" />
        </a>
      </div>
    </div>
  );
}
