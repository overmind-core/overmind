import { useState } from "react";

import { useRouterState } from "@tanstack/react-router";

import apiClient from "@/client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { FeedbackAreaEnum } from "@/openapi/models/FeedbackAreaEnum";
import { FeedbackKindEnum } from "@/openapi/models/FeedbackKindEnum";

interface FeedbackDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type SubmitState = "idle" | "submitting" | "submitted" | "error";

const DISCORD_URL = "https://discord.gg/TPF722ZKuj";

// The kinds a person reports; coding agents also report mismatches and dead ends.
const KINDS: { value: FeedbackKindEnum; label: string; placeholder: string }[] = [
  {
    label: "Something failed",
    placeholder: "What you did, what you expected and what happened.",
    value: FeedbackKindEnum.failure,
  },
  {
    label: "Unclear",
    placeholder: "The screen, message or document, and the part that is unclear.",
    value: FeedbackKindEnum.confusing,
  },
  {
    label: "Missing capability",
    placeholder: "What you need to do and why.",
    value: FeedbackKindEnum.missing,
  },
  {
    label: "Slow or stuck",
    placeholder: "The operation, how long it took and what it showed.",
    value: FeedbackKindEnum.stuck,
  },
  {
    label: "Idea",
    placeholder: "The change and the problem it solves.",
    value: FeedbackKindEnum.idea,
  },
  { label: "Worked well", placeholder: "What worked.", value: FeedbackKindEnum.praise },
  { label: "Question", placeholder: "Your question.", value: FeedbackKindEnum.question },
];

const AREAS: { value: FeedbackAreaEnum; label: string; path?: RegExp }[] = [
  {
    label: "Setup and connection",
    path: /^\/(projects|capabilities)?\/?$/,
    value: FeedbackAreaEnum.setup,
  },
  { label: "Observability", path: /^\/observability/, value: FeedbackAreaEnum.observability },
  { label: "Datasets", path: /^\/datasets/, value: FeedbackAreaEnum.datasets },
  { label: "Evaluation", path: /^\/evaluations/, value: FeedbackAreaEnum.evaluation },
  { label: "Training", path: /^\/training/, value: FeedbackAreaEnum.training },
  { label: "Serving", path: /^\/inference/, value: FeedbackAreaEnum.serving },
  { label: "Billing", path: /^\/settings/, value: FeedbackAreaEnum.billing },
  { label: "Other", value: FeedbackAreaEnum.other },
];

const DEFAULT_KIND = FeedbackKindEnum.failure;

function areaFor(pathname: string): FeedbackAreaEnum {
  return AREAS.find((area) => area.path?.test(pathname))?.value ?? FeedbackAreaEnum.other;
}

function DiscordLink() {
  return (
    <a
      className="flex items-center justify-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
      href={DISCORD_URL}
      rel="noreferrer"
      target="_blank"
    >
      <Icon.discord className="size-4" />
      Discord community
    </a>
  );
}

export function FeedbackDialog({ open, onOpenChange }: FeedbackDialogProps) {
  const location = useRouterState({ select: (s) => s.location });
  const projectId = (location.search as { projectId?: string }).projectId;
  const [kind, setKind] = useState<FeedbackKindEnum>(DEFAULT_KIND);
  const [area, setArea] = useState<FeedbackAreaEnum | null>(null);
  const [message, setMessage] = useState("");
  const [submitState, setSubmitState] = useState<SubmitState>("idle");

  const activeKind = KINDS.find((item) => item.value === kind) ?? KINDS[0];
  const selectedArea = area ?? areaFor(location.pathname);

  const handleSubmit = async () => {
    if (!message.trim()) return;
    setSubmitState("submitting");
    try {
      await apiClient.feedback.feedbackCreate({
        feedbackCreateRequest: {
          area: selectedArea,
          kind,
          message: message.trim(),
          page: location.pathname,
          project: projectId ?? null,
        },
      });
      setSubmitState("submitted");
    } catch {
      setSubmitState("error");
    }
  };

  const handleClose = () => {
    onOpenChange(false);
    setTimeout(() => {
      setMessage("");
      setKind(DEFAULT_KIND);
      setArea(null);
      setSubmitState("idle");
    }, 200);
  };

  const isPending = submitState === "submitting";
  const isSubmitted = submitState === "submitted";

  return (
    <Dialog onOpenChange={(o) => !o && handleClose()} open={open}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Feedback & support</DialogTitle>
          <DialogDescription>
            {isSubmitted ? "Sent to the Overmind team." : "Goes to the Overmind team."}
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {!isSubmitted && (
            <div className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="feedback-kind">Kind</Label>
                  <Select
                    disabled={isPending}
                    onValueChange={(value) => setKind(value as FeedbackKindEnum)}
                    value={kind}
                  >
                    <SelectTrigger className="w-full" id="feedback-kind">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {KINDS.map((item) => (
                        <SelectItem key={item.value} value={item.value}>
                          {item.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="feedback-area">Area</Label>
                  <Select
                    disabled={isPending}
                    onValueChange={(value) => setArea(value as FeedbackAreaEnum)}
                    value={selectedArea}
                  >
                    <SelectTrigger className="w-full" id="feedback-area">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {AREAS.map((item) => (
                        <SelectItem key={item.value} value={item.value}>
                          {item.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="feedback-text">Message</Label>
                <Textarea
                  autoFocus
                  className="min-h-[140px] resize-none"
                  disabled={isPending}
                  id="feedback-text"
                  maxLength={5000}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder={activeKind.placeholder}
                  value={message}
                />
                {submitState === "error" && (
                  <p className="text-sm text-muted-foreground">Not sent. Try again.</p>
                )}
              </div>

              <Button
                className="w-full"
                disabled={!message.trim() || isPending}
                onClick={handleSubmit}
                size="lg"
              >
                <Icon.send />
                {isPending ? "Sending…" : "Submit feedback"}
              </Button>

              <Separator className="-mx-5 data-[orientation=horizontal]:w-auto" />
              <DiscordLink />
            </div>
          )}

          {isSubmitted && (
            <div className="space-y-4">
              <Button className="w-full" onClick={handleClose} size="lg">
                <Icon.close />
                Close
              </Button>
              <Separator className="-mx-5 data-[orientation=horizontal]:w-auto" />
              <DiscordLink />
            </div>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
