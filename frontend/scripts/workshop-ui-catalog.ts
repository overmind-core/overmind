import { readFile, writeFile } from "node:fs/promises";
import { z } from "zod/v4";

import { workshopCatalog } from "../src/components/datasets/workshop-ui-catalog";

const catalog = Object.fromEntries(
  Object.entries(workshopCatalog).map(([name, entry]) => [
    name,
    {
      description: entry.description,
      props: z.toJSONSchema(entry.props),
    },
  ])
);
const path = new URL("../../workshop_runtime/presentation-catalog.json", import.meta.url);
const content = `${JSON.stringify(catalog, null, 2)}\n`;
if (process.argv.includes("--check")) {
  if ((await readFile(path, "utf8")) !== content)
    throw new Error(
      "Run bun scripts/workshop-ui-catalog.ts to update the Workshop presentation catalog."
    );
} else await writeFile(path, content);
