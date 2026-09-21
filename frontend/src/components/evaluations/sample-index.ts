import type { EvalSampleList } from "@/openapi";

export interface DatapointRow {
  key: string;
  label: string;
  inputSampleId: string;
}

type SampleIdentity = Pick<EvalSampleList, "id" | "variant" | "rowIndex" | "sourceTraceId">;

export function indexSamples(samples: SampleIdentity[]) {
  const sampleMap = new Map<string, Map<string, string>>();
  const rows = new Map<string, DatapointRow>();
  const sampleVariantMap = new Map<string, string>();

  for (const sample of samples) {
    // Dataset rows align across variants; generated trace IDs do not.
    const key =
      sample.rowIndex != null
        ? `row:${sample.rowIndex}`
        : sample.sourceTraceId
          ? `trace:${sample.sourceTraceId}`
          : `sample:${sample.id}`;
    if (!sampleMap.has(key)) sampleMap.set(key, new Map());
    sampleMap.get(key)!.set(sample.variant, sample.id);
    sampleVariantMap.set(sample.id, sample.variant);
    if (!rows.has(key)) {
      rows.set(key, {
        inputSampleId: sample.id,
        key,
        label:
          sample.rowIndex != null
            ? String(sample.rowIndex + 1)
            : (sample.sourceTraceId || sample.id).slice(0, 8),
      });
    }
  }

  return { datapointRows: [...rows.values()], sampleMap, sampleVariantMap };
}
