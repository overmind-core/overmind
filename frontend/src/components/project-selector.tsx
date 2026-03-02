import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useProjectsList } from "@/hooks/use-projects";

export function ProjectSelector({ selection, setSelection }: {
  selection: string | undefined;
  setSelection: (projectId: string | undefined) => void;
}) {
  const { data } = useProjectsList();
  if (data?.projects?.length && data?.projects?.length <= 1) {
    return null;
  }
  return (
    <Select onValueChange={setSelection} value={selection}>
      <SelectTrigger size="sm">
        <SelectValue placeholder="All projects" />
      </SelectTrigger>
      <SelectContent>
        {data?.projects?.map((p) => (
          <SelectItem key={p.projectId} value={p.projectId}>
            {p.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
