import { useState } from "react";

import { useQueryClient } from "@tanstack/react-query";

import api from "@/client";
import { ModelOptionLabel } from "@/components/model-option-label";
import { getProviderIcon, ProviderLogo } from "@/components/model-provider-chip";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SectionCard } from "@/components/ui/section-card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import {
  useChatGPTModels,
  useSaveWorkshopFunding,
  useWorkshopFunding,
  workshopFundingKey,
} from "@/hooks/use-workshop-funding";
import { errorMessage } from "@/lib/notify";
import { FundingSourceEnum } from "@/openapi";

export function ChatGPTSettings() {
  const funding = useWorkshopFunding();
  const save = useSaveWorkshopFunding();
  const cache = useQueryClient();
  const [selection, setSelection] = useState<string | undefined>();
  const [modelSelection, setModelSelection] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const data = funding.data;
  const accountId = selection ?? data?.accountId ?? data?.accounts[0]?.id;
  const account = data?.accounts.find((item) => item.id === accountId);
  const models = useChatGPTModels(accountId, !!data?.enabled && !!account?.planEnabled);
  const model = modelSelection ?? (data?.accountId === accountId ? data?.model : "");
  const pending = busy || save.isPending;
  const local = window.location.hostname === "127.0.0.1" && window.location.protocol === "http:";
  const loopback = new URL(window.location.href);
  loopback.hostname = "127.0.0.1";

  async function perform(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (err) {
      setError(errorMessage(err, "Could not update the ChatGPT connection."));
    } finally {
      setBusy(false);
    }
  }

  async function connect(id?: string) {
    await perform(async () => {
      const result = await api.chatgpt.chatgptStartCreate(
        { chatGPTStartRequestRequest: { accountId: id } },
        { credentials: "include" }
      );
      window.location.assign(result.authorizationUrl);
    });
  }

  async function disconnect() {
    if (!accountId) return;
    await perform(async () => {
      const result = await api.chatgpt.chatgptDisconnectCreate({
        chatGPTAccountRequestRequest: { accountId },
      });
      await cache.invalidateQueries({ queryKey: workshopFundingKey });
      cache.removeQueries({ queryKey: ["chatgpt-models", accountId] });
      if (!result.revocationConfirmed) {
        setNotice(
          "Disconnected locally. Remote revocation was not confirmed; disconnect Overmind in ChatGPT Settings."
        );
      }
    });
  }

  if (import.meta.env.VITE_SELF_HOSTED !== "true" || (data && !data.enabled)) return null;
  return (
    <SectionCard
      description="Choose how your Workshop requests are funded. Changes apply to your next turn."
      title="Data Workshop models"
    >
      <div className="space-y-4">
        {funding.isLoading ? <Spinner /> : null}
        {funding.error ? (
          <Alert variant="destructive">
            <div className="min-w-0 flex-1">
              {errorMessage(funding.error, "Could not load Workshop funding.")}
            </div>
          </Alert>
        ) : null}
        {data ? (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="space-y-1">
                <Badge variant="neutral">
                  {data.fundingSource === "chatgpt" ? "ChatGPT plan" : "Server models"}
                </Badge>
                <p className="text-sm text-muted-foreground">
                  {data.fundingSource === "chatgpt"
                    ? "No Overmind credits. ChatGPT plan and app limits apply."
                    : "Uses the model providers configured on this server."}
                </p>
              </div>
              {data.fundingSource === "chatgpt" ? (
                <Button
                  disabled={pending}
                  onClick={() =>
                    void perform(() =>
                      save.mutateAsync({ fundingSource: FundingSourceEnum.platform })
                    )
                  }
                  size="sm"
                  variant="secondary"
                >
                  Use server models
                </Button>
              ) : null}
            </div>
            {!local ? (
              <Alert>
                <div className="min-w-0 flex-1">
                  Open this installation on 127.0.0.1 to connect ChatGPT. For a remote installation,
                  use an SSH tunnel for the Console and API.
                </div>
                {window.location.hostname === "localhost" &&
                window.location.protocol === "http:" ? (
                  <Button asChild className="mt-3" size="sm" variant="secondary">
                    <a href={loopback.href}>Open local Console</a>
                  </Button>
                ) : null}
              </Alert>
            ) : null}
            {data.accounts.length ? (
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <label className="text-sm" htmlFor="chatgpt-account">
                    ChatGPT account
                  </label>
                  <Select
                    disabled={pending}
                    onValueChange={(value) => {
                      setSelection(value);
                      setModelSelection(undefined);
                    }}
                    value={accountId}
                  >
                    <SelectTrigger id="chatgpt-account">
                      <SelectValue placeholder="Choose an account" />
                    </SelectTrigger>
                    <SelectContent>
                      {data.accounts.map((item) => (
                        <SelectItem key={item.id} value={item.id}>
                          {item.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {account?.planEnabled ? (
                  <div className="space-y-1.5">
                    <label className="text-sm" htmlFor="chatgpt-model">
                      Model
                    </label>
                    <Select
                      disabled={pending || models.isPending}
                      onValueChange={setModelSelection}
                      value={model || undefined}
                    >
                      <SelectTrigger id="chatgpt-model">
                        <SelectValue
                          placeholder={models.isPending ? "Loading models…" : "Choose a model"}
                        />
                      </SelectTrigger>
                      <SelectContent>
                        {models.data?.map((item) => (
                          <SelectItem key={item.id} value={item.id}>
                            <ModelOptionLabel model={item.id} name={item.name} />
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                ) : null}
              </div>
            ) : null}
            {account && !account.planEnabled ? (
              <p className="text-sm text-warning">
                {account.connected
                  ? "ChatGPT plan usage has not been authorised."
                  : "This ChatGPT account is disconnected."}
              </p>
            ) : null}
            {models.error ? (
              <Alert variant="destructive">
                <div className="min-w-0 flex-1">
                  {errorMessage(models.error, "Could not load ChatGPT models.")}
                </div>
                <Button
                  className="mt-3"
                  onClick={() => void models.refetch()}
                  size="sm"
                  variant="secondary"
                >
                  Retry models
                </Button>
              </Alert>
            ) : null}
            <div className="flex flex-wrap items-center gap-2">
              {account?.planEnabled ? (
                <Button
                  disabled={pending || !model || !models.data?.some((item) => item.id === model)}
                  onClick={() =>
                    void perform(() =>
                      save.mutateAsync({
                        accountId,
                        fundingSource: FundingSourceEnum.chatgpt,
                        model,
                      })
                    )
                  }
                  size="sm"
                >
                  Use ChatGPT plan
                </Button>
              ) : null}
              <Button
                disabled={pending || !local}
                onClick={() => void connect(account?.id)}
                size="sm"
                variant="secondary"
              >
                <ProviderLogo
                  Icon={getProviderIcon("openai")}
                  providerLabel="OpenAI"
                  providerSlug="openai"
                />
                Continue with ChatGPT
              </Button>
              {account ? (
                <Button
                  disabled={pending || !local}
                  onClick={() => void connect()}
                  size="sm"
                  variant="secondary"
                >
                  Add account
                </Button>
              ) : null}
              {account?.connected ? (
                <Button
                  disabled={pending}
                  onClick={() => void disconnect()}
                  size="sm"
                  variant="secondary"
                >
                  Disconnect
                </Button>
              ) : null}
              {pending ? <Spinner size="sm" /> : null}
              <a
                className="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground"
                href={data.usageUrl}
                rel="noreferrer"
                target="_blank"
              >
                Manage ChatGPT usage
              </a>
            </div>
            <p className="text-xs text-muted-foreground">
              Includes Workshop generation and semantic checks. Evaluation runs, training and
              serving use their existing billing. ChatGPT limits stop the request.
            </p>
          </>
        ) : null}
        {error ? (
          <Alert variant="destructive">
            <div className="min-w-0 flex-1">{error}</div>
          </Alert>
        ) : null}
        {notice ? (
          <Alert>
            <div className="min-w-0 flex-1">{notice}</div>
          </Alert>
        ) : null}
      </div>
    </SectionCard>
  );
}
