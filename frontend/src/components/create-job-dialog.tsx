import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

const JOB_TYPES = [
  { label: "Template Extraction", value: "template_extraction" },
  { label: "Scoring", value: "scoring" },
  { label: "Prompt Tuning", value: "prompt_tuning" },
] as const;

export function CreateJobDialog() {
  const [open, setOpen] = useState(false);
  const [jobType, setJobType] = useState<string>("");
  const [agent, setAgent] = useState<string>("");
  const [prompt, setPrompt] = useState<string>("");
  const [description, setDescription] = useState("");

  const handleClose = () => setOpen(false);

  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>
        <Button variant="outline">Create Job</Button>
      </DialogTrigger>
      <DialogContent className="max-w-md sm:max-w-[540px]">
        <DialogHeader>
          <DialogTitle>Create Job</DialogTitle>
          <DialogDescription>Configure and launch a new background job.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="space-y-2">
            <Label>Job Type</Label>
            <Select onValueChange={setJobType} value={jobType}>
              <SelectTrigger>
                <SelectValue placeholder="Choose type" />
              </SelectTrigger>
              <SelectContent>
                {JOB_TYPES.map((t) => (
                  <SelectItem key={t.value} value={t.value}>
                    {t.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>Agent</Label>
            <Select onValueChange={setAgent} value={agent}>
              <SelectTrigger>
                <SelectValue placeholder="All Agents" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Agents</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>Prompt</Label>
            <Select onValueChange={setPrompt} value={prompt}>
              <SelectTrigger>
                <SelectValue placeholder="All Prompts" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Prompts</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>Description</Label>
            <Textarea
              className="min-h-[80px]"
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional description"
              rows={3}
              value={description}
            />
          </div>
        </div>
        <DialogFooter>
          <Button onClick={handleClose} variant="ghost">
            Cancel
          </Button>
          <Button onClick={handleClose}>Create</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
