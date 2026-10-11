import { describe, expect, it } from "vitest";

import { configurationSections, configurationValue } from "./run-configuration";

// Launch settings can differ from mutable job fields; pending runtime values,
// nested provider options, false/zero and empty values must remain distinguishable.
describe("recorded run configuration", () => {
  it("keeps launch settings separate from resolved worker parameters", () => {
    const sections = configurationSections({
      baseModel: "base/model",
      hyperparameters: { learning_rate: 0.9 },
      provider: "modal",
      record: {
        effective: { parameters: { LEARNING_RATE: "0.0001", PER_DEVICE_BATCH: "8" } },
      },
      requestedConfiguration: {
        configuration: {
          hyperparameters: {
            checkpoint_policy: { fractions: [0.25, 1] },
            custom: { stages: [{ enabled: false, name: "first" }] },
            learning_rate: 0.000123456,
            monitoring: { early_stopping: null, generation: { labels: ["No", "Yes"] } },
            packing: false,
            weight_decay: 0,
          },
        },
        custom_launch_field: "retained",
        selection: { train_fingerprint: "complete-fingerprint", train_rows: 1034657 },
      },
    });
    const values = sections.flatMap((section) => section.rows.map((row) => row.value));
    expect(values).toContain(0.000123456);
    expect(values).not.toContain(0.9);
    expect(values).toContain("0.0001");
    expect(values).toContain("8");
    expect(values).toContain(false);
    expect(values).toContain(0);
    expect(values).toContain(null);
    expect(values).toContain("first");
    expect(values).toContain("retained");
    expect(values).toContain("complete-fingerprint");
    expect(values).toContainEqual(["No", "Yes"]);
    expect(values).toContainEqual([0.25, 1]);
    expect(
      sections.find((section) => section.title === "Effective configuration")?.rows
    ).toHaveLength(2);
  });

  it("labels job settings without a launch receipt and leaves effective settings unknown", () => {
    const sections = configurationSections({
      baseModel: "base/model",
      hyperparameters: { n_epochs: 2 },
      provider: "modal",
      record: null,
      requestedConfiguration: null,
    });
    expect(sections.find((section) => section.title === "Training parameters")?.source).toBe(
      "Job record"
    );
    expect(sections.find((section) => section.title === "Effective configuration")?.rows).toEqual(
      []
    );
  });

  it("formats values without rounding away requested precision or hiding empty states", () => {
    expect(configurationValue(0.000123456789)).toBe("0.000123456789");
    expect(configurationValue(0)).toBe("0");
    expect(configurationValue(false)).toBe("Disabled");
    expect(configurationValue(null)).toBe("Not set");
    expect(configurationValue(undefined)).toBe("Not recorded");
    expect(configurationValue("")).toBe("Empty");
    expect(configurationValue([])).toBe("None");
    expect(configurationValue(["a", "b"])).toBe("a\nb");
  });
});
