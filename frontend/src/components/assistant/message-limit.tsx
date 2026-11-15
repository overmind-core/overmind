import { MESSAGE_CHARS } from "./types";

/** The input takes no more than the API accepts: say so where the text stops. */
export function MessageLimit() {
  return (
    <p className="pt-1 text-xs text-warning" role="status">
      Message limit reached: {MESSAGE_CHARS.toLocaleString("en-US")} characters
    </p>
  );
}
