// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { TrajectoryCondition } from "./TrajectoryConnector";

afterEach(cleanup);

it("keeps conditions collapsed and reveals the full text on click", () => {
  const label = "The normalized assistant message contains tool calls.";
  render(<TrajectoryCondition basis="code" label={label} x={100} y={200} />);
  expect(screen.queryByText(label)).toBeNull();
  const trigger = screen.getByRole("button", { name: `Show condition: ${label}` });
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(trigger);
  expect(screen.getByText(label)).toBeTruthy();
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(trigger.parentElement?.contains(screen.getByText(label))).toBe(true);
  fireEvent.click(trigger);
  expect(screen.queryByText(label)).toBeNull();
});
