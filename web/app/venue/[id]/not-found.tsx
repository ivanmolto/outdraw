import { Message, Shell } from "./shell";

/** Rendered by `notFound()` in page.tsx: an unknown or malformed venue id. */
export default function VenueNotFound() {
  return (
    <Shell>
      <Message
        title="Venue not found"
        body="There's no venue at this address. It may have been removed, or the link is wrong."
      />
    </Shell>
  );
}
