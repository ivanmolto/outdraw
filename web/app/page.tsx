import { redirect } from "next/navigation";

// The root has no page of its own yet: a visitor lands on the venue setup, the first step of
// the flow. `redirect` throws, so this never renders.
export default function Home(): never {
  redirect("/venue/new");
}
