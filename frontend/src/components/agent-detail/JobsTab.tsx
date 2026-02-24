import { Loader2 } from "lucide-react";

import type { JobOut } from "@/api";
import { JobCard } from "@/components/jobs/JobCard";

export function JobsTab({ jobs }: { jobs: JobOut[] }) {
  if (jobs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center border border-dashed border-border py-16">
        <Loader2 className="mb-3 size-12 text-muted-foreground/50" />
        <p className="max-w-sm text-center text-sm italic text-muted-foreground">
          No jobs have been run for this agent yet.
          <br />
          Use one of the available actions above to start a job.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {jobs.map((j) => (
        <JobCard job={j} key={j.jobId} />
      ))}
    </div>
  );
}
