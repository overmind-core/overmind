import { useState } from "react";

import { useNavigate } from "@tanstack/react-router";

import { ModelOptionLabel } from "@/components/model-option-label";
import { getProviderIcon, ProviderLogo } from "@/components/model-provider-chip";
import { Button } from "@/components/ui/button";
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
} from "@/hooks/use-workshop-funding";
import { errorMessage } from "@/lib/notify";
import { FundingSourceEnum, type WorkshopFunding } from "@/openapi";

export function WorkshopFundingControl({ disabled = false }: { disabled?: boolean }) {
  const { data, isChanging } = useWorkshopFunding();
  if (!data?.enabled) return null;
  return <FundingControl disabled={disabled || isChanging} funding={data} />;
}

function FundingControl({ disabled, funding }: { disabled: boolean; funding: WorkshopFunding }) {
  const save = useSaveWorkshopFunding();
  const navigate = useNavigate();
  const [choosing, setChoosing] = useState(false);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const active = funding.fundingSource === "chatgpt";
  const account = funding.accountId
    ? funding.accounts.find((item) => item.id === funding.accountId)
    : funding.accounts.find((item) => item.planEnabled);
  const models = useChatGPTModels(account?.id, !!account?.planEnabled);
  const model = funding.accountId === account?.id ? funding.model : "";
  const selected = models.data?.find((item) => item.id === model);
  const settings = () => void navigate({ to: "/settings" });

  async function change(nextModel?: string) {
    setError("");
    try {
      await save.mutateAsync(
        nextModel
          ? {
              accountId: account?.id,
              fundingSource: FundingSourceEnum.chatgpt,
              model: nextModel,
            }
          : { fundingSource: FundingSourceEnum.platform }
      );
      setChoosing(false);
      setOpen(false);
    } catch (err) {
      setError(errorMessage(err, "Could not change Workshop funding."));
    }
  }

  function toggle() {
    if (active) {
      void change();
    } else if (!account?.planEnabled) {
      settings();
    } else if (model) {
      void change(model);
    } else {
      setChoosing(true);
      setOpen(true);
    }
  }

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1.5">
      <Button
        aria-label="Use ChatGPT"
        aria-pressed={active}
        disabled={disabled}
        onClick={toggle}
        size="xs"
        title={active ? "Use server models instead" : "Use your ChatGPT plan"}
        type="button"
        variant={active ? "default" : "secondary"}
      >
        <ProviderLogo
          Icon={getProviderIcon("openai")}
          providerLabel="OpenAI"
          providerSlug="openai"
        />
        Use ChatGPT
      </Button>
      {(active || choosing) && account?.planEnabled ? (
        <Select
          disabled={disabled}
          onOpenChange={setOpen}
          onValueChange={(value) => void change(value)}
          open={open}
          value={model || undefined}
        >
          <SelectTrigger aria-label="ChatGPT model" className="max-w-48" size="xs">
            <SelectValue placeholder="Choose model">
              {model ? <ModelOptionLabel model={model} name={selected?.name} /> : undefined}
            </SelectValue>
          </SelectTrigger>
          <SelectContent align="start" position="popper" side="top">
            {models.isPending ? (
              <div
                className="flex items-center gap-2 p-2 text-xs text-muted-foreground"
                role="status"
              >
                <Spinner size="sm" />
                Loading models
              </div>
            ) : null}
            {models.data?.map((item) => (
              <SelectItem key={item.id} textValue={item.name} value={item.id}>
                <ModelOptionLabel model={item.id} name={item.name} />
              </SelectItem>
            ))}
            {!models.isPending && !models.error && !models.data?.length ? (
              <p className="p-2 text-xs text-muted-foreground">No models available</p>
            ) : null}
            {models.error ? (
              <div className="space-y-2 p-2">
                <p className="text-xs text-destructive" role="alert">
                  Could not load ChatGPT models.
                </p>
                <Button
                  onClick={() => void models.refetch()}
                  size="xs"
                  type="button"
                  variant="secondary"
                >
                  Retry models
                </Button>
              </div>
            ) : null}
          </SelectContent>
        </Select>
      ) : null}
      {active && !account?.planEnabled ? (
        <Button
          aria-label="Reconnect ChatGPT"
          disabled={disabled}
          onClick={settings}
          size="xs"
          type="button"
          variant="secondary"
        >
          Reconnect
        </Button>
      ) : null}
      {save.isPending ? <Spinner size="sm" /> : null}
      {error ? (
        <p className="basis-full break-words text-xs text-destructive" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
