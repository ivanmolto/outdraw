import { Shell } from "../shell";
import { ShortlistSkeleton } from "./shortlist-view";

export default function Loading() {
  return (
    <Shell>
      <ShortlistSkeleton />
    </Shell>
  );
}
