import { formatCost } from "@/lib/formatters";

export function CostComparison({ cost, delta }: { cost?: number | null; delta?: number | null }) {
  if (cost == null) return <>Cost unavailable</>;
  return (
    <>
      {cost === 0 ? "$0" : formatCost(cost)}
      {delta != null && (
        <>
          {" "}
          ({delta === 0 ? "no change" : `${delta > 0 ? "+" : "−"}${formatCost(Math.abs(delta))}`})
        </>
      )}
    </>
  );
}
