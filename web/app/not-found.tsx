import { Message, Shell } from "./venue/[id]/shell";

/** The app's not-found page: an unknown URL, and what proxy.ts rewrites an unknown demo
 * slug to (`/v/{slug}` outside the seeds list), so those answer a real 404. */
export default function NotFound() {
  return (
    <Shell>
      <Message
        title="Page not found"
        body="There's nothing at this address. Pick a demo venue from the home page, or set up your own."
      />
    </Shell>
  );
}
