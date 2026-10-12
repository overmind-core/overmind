import { useState } from "react";

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
import { FeedbackCreateCategoryEnum as FeedbackCategoryEnum } from "@/openapi/models/FeedbackCreateCategoryEnum";

interface FeedbackDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type SubmitState = "idle" | "submitting" | "submitted" | "error";

const DISCORD_URL = "https://discord.gg/TPF722ZKuj";

const CATEGORIES: {
  value: FeedbackCategoryEnum;
  label: string;
  placeholder: string;
}[] = [
  {
    label: "Report bug",
    placeholder: "What went wrong? Steps to reproduce help us fix it faster…",
    value: FeedbackCategoryEnum.bug,
  },
  {
    label: "Suggest improvement",
    placeholder: "What could work better? Tell us what you'd like to see improved…",
    value: FeedbackCategoryEnum.improvement,
  },
  {
    label: "Ask a question",
    placeholder: "What's on your mind? We're happy to help…",
    value: FeedbackCategoryEnum.question,
  },
  {
    label: "Share what you love",
    placeholder: "Tell us what's working well — we love hearing it…",
    value: FeedbackCategoryEnum.love,
  },
  {
    label: "Request feature",
    placeholder: "What feature would you love to see? Describe the use case…",
    value: FeedbackCategoryEnum.feature,
  },
  {
    label: "Performance issue",
    placeholder: "Where is it slow? What were you doing when it happened…",
    value: FeedbackCategoryEnum.performance,
  },
  {
    label: "Accessibility issue",
    placeholder: "Tell us where we fell short — we want this to work for everyone…",
    value: FeedbackCategoryEnum.accessibility,
  },
  {
    label: "Security concern",
    placeholder: "Describe the concern. For sensitive reports, please email security@…",
    value: FeedbackCategoryEnum.security,
  },
  {
    label: "Documentation issue",
    placeholder: "What was unclear, missing, or wrong in the docs…",
    value: FeedbackCategoryEnum.documentation,
  },
  {
    label: "Other",
    placeholder: "Tell us what you're thinking…",
    value: FeedbackCategoryEnum.other,
  },
];

const DEFAULT_CATEGORY: FeedbackCategoryEnum = FeedbackCategoryEnum.improvement;

export function FeedbackDialog({ open, onOpenChange }: FeedbackDialogProps) {
  const [category, setCategory] = useState<FeedbackCategoryEnum>(DEFAULT_CATEGORY);
  const [feedback, setFeedback] = useState("");
  const [submitState, setSubmitState] = useState<SubmitState>("idle");

  const activeCategory = CATEGORIES.find((c) => c.value === category) ?? CATEGORIES[0];

  const handleSubmit = async () => {
    if (!feedback.trim()) return;
    setSubmitState("submitting");
    try {
      await apiClient.feedback.feedbackCreate({
        feedbackCreateRequest: { category, feedback: feedback.trim() },
      });
      setSubmitState("submitted");
    } catch {
      setSubmitState("error");
    }
  };

  const handleClose = () => {
    onOpenChange(false);
    setTimeout(() => {
      setFeedback("");
      setCategory(DEFAULT_CATEGORY);
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
            {isSubmitted
              ? "Thanks for reaching out — we really appreciate it."
              : "Have a question, found a bug, or want to share an idea? We read everything."}
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {!isSubmitted && (
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="feedback-category">Category</Label>
                <Select
                  disabled={isPending}
                  onValueChange={(v) => setCategory(v as FeedbackCategoryEnum)}
                  value={category}
                >
                  <SelectTrigger className="w-full" id="feedback-category">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CATEGORIES.map((c) => (
                      <SelectItem key={c.value} value={c.value}>
                        {c.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="feedback-text">Your message</Label>
                <Textarea
                  autoFocus
                  className="min-h-[140px] resize-none"
                  disabled={isPending}
                  id="feedback-text"
                  onChange={(e) => setFeedback(e.target.value)}
                  placeholder={activeCategory.placeholder}
                  value={feedback}
                />
                {submitState === "error" && (
                  <p className="text-sm text-destructive">
                    Something went wrong — please try again.
                  </p>
                )}
              </div>

              <Button
                className="w-full"
                disabled={!feedback.trim() || isPending}
                onClick={handleSubmit}
                size="lg"
              >
                <Icon.send />
                {isPending ? "Sending…" : "Submit feedback"}
              </Button>

              <Separator className="-mx-5 data-[orientation=horizontal]:w-auto" />

              <a
                className="flex items-center justify-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
                href={DISCORD_URL}
                rel="noreferrer"
                target="_blank"
              >
                <Icon.discord className="size-4" />
                Join our Discord community
              </a>
            </div>
          )}

          {isSubmitted && (
            <div className="space-y-4">
              <Button className="w-full" onClick={handleClose} size="lg">
                <Icon.close />
                Close
              </Button>
              <Separator className="-mx-5 data-[orientation=horizontal]:w-auto" />
              <a
                className="flex items-center justify-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
                href={DISCORD_URL}
                rel="noreferrer"
                target="_blank"
              >
                <Icon.discord className="size-4" />
                Join our Discord community
              </a>
            </div>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
