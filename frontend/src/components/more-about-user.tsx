import { useEffect, useState } from "react";

import { useNavigate } from "@tanstack/react-router";

import apiClient from "@/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useOnboardingQuery } from "@/hooks/use-query";
import { cn } from "@/lib/utils";

const PRIORITY_OPTIONS = [
  { key: "latency", label: "Latency" },
  { key: "cost", label: "Cost" },
  { key: "accuracy", label: "Accuracy" },
  { key: "privacy", label: "Privacy" },
];

export function MoreAboutUser() {
  const [priorityChecked, setPriorityChecked] = useState<string[]>([]);
  const [description, setDescription] = useState("");
  const [justSubmitted, setJustSubmitted] = useState(false);
  const navigate = useNavigate();

  const { data } = useOnboardingQuery();

  useEffect(() => {
    if (data) {
      setPriorityChecked(data.priorities ?? []);
      setDescription(data.description ?? "");
    }
  }, [data]);

  const handlePriorityCheck = (key: string) =>
    setPriorityChecked((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );

  const handleSubmit = () => {
    setJustSubmitted(true);
    apiClient.onboarding.createUserOnboardingApiV1OnboardingPost({
      userOnboardingRequest: { description, priorities: priorityChecked },
    });

    setTimeout(() => {
      setJustSubmitted(false);
      navigate({ to: "/" });
    }, 1800);
  };

  const canSubmit = priorityChecked.length > 0 || description.trim().length > 0;

  return (
    <Card>
      <CardContent className="space-y-6 pt-6">
        <div className="space-y-3">
          <Label className="text-base font-semibold">What matters most to you?</Label>
          <p className="text-sm text-muted-foreground">Select one or more priorities.</p>
          <div className="flex flex-wrap gap-2">
            {PRIORITY_OPTIONS.map((opt) => {
              const isChecked = priorityChecked.includes(opt.key);
              return (
                <button
                  aria-pressed={isChecked}
                  className={cn(
                    "inline-flex items-center gap-2 rounded-lg border px-4 py-2.5 text-sm font-medium transition-all",
                    isChecked
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border bg-muted/30 hover:border-primary/50 hover:bg-muted/50"
                  )}
                  key={opt.key}
                  onClick={() => handlePriorityCheck(opt.key)}
                  type="button"
                >
                  {isChecked && <span className="size-1.5 shrink-0 rounded-full bg-primary" />}
                  {opt.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="space-y-3">
          <Label className="text-base font-semibold" htmlFor="agent-description">
            What is your agent for?
          </Label>
          <Textarea
            className="min-h-[100px] resize-none"
            id="agent-description"
            onChange={(e) => setDescription(e.target.value)}
            placeholder="E.g. Summarizing legal contracts, detecting bias, redacting customer data..."
            rows={4}
            value={description}
          />
        </div>

        <div className="flex justify-between gap-3 pt-2">
          <Button
            onClick={() => {
              setPriorityChecked([]);
              setDescription("");
            }}
            variant="ghost"
          >
            Clear
          </Button>
          <Button
            className={cn(justSubmitted && "bg-emerald-600 hover:bg-emerald-700")}
            disabled={!canSubmit}
            onClick={handleSubmit}
          >
            {justSubmitted ? "Submitted!" : "Finish"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
