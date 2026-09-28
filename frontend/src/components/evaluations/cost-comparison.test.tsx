// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { CostComparison } from "./cost-comparison";

afterEach(cleanup);

it.each([
  [undefined, undefined, "Cost unavailable"],
  [0, 0, "$0 (no change)"],
  [1, 0.25, "$1.00 (+$0.250)"],
  [1, -0.25, "$1.00 (−$0.250)"],
])("shows the estimate and signed difference", (cost, delta, expected) => {
  const { container } = render(<CostComparison cost={cost} delta={delta} />);
  expect(container.textContent).toBe(expected);
});
