import { useEffect, useState, useSyncExternalStore } from "react";
import { Link, useParams } from "react-router-dom";
import { OntologyReviewView } from "@/components/OntologyReviewView";
import { Page, PageHeader } from "@/components/PageShell";
import { useApp } from "@/context/AppContext";
import { createReview, type Review } from "@/lib/ontologyReview";

/**
 * ONTO-PROFILE-03 (dms#516). The steward review of what the profiler proposed
 * for a file Space: keys and links, each with its counts in view.
 *
 * This file only wires. The rules live in `createReview` (lib/ontologyReview.ts)
 * and what is shown lives in `OntologyReviewView`, and both are tested by
 * `npm test`. One review object per Space: when the page moves to another
 * Space the old one is disposed, so a late answer for it changes nothing.
 */
export function OntologyReviewPage() {
  const { spaceId = "" } = useParams();
  const { spaces } = useApp();
  const [review, setReview] = useState<Review | null>(null);

  useEffect(() => {
    const next = createReview(spaceId);
    setReview(next);
    void next.load();
    return () => next.dispose();
  }, [spaceId]);

  return (
    <Page>
      <div data-testid="onto-review-page">
        <PageHeader
          phase="Steward · review"
          title="Review keys and links"
          blurb="The system counted this Space's uploaded files and proposes which columns identify a row and which columns point at another table. Nothing proposed here is used in an answer until a steward approves it, one item at a time, with the counts in view."
          actions={
            <Link
              to="/spaces"
              className="inline-flex min-h-11 items-center border border-[var(--color-line)] px-3 text-sm hover:border-[var(--color-accent)]"
            >
              Back to Spaces
            </Link>
          }
        />
        {/* Never another Space's review under this Space's address. */}
        {review && review.getState().spaceId === spaceId ? (
          <LiveReview review={review} spaceName={spaces.find((s) => s.id === spaceId)?.name ?? null} />
        ) : (
          <p className="mt-6 text-sm text-[var(--color-ink-muted)]">Loading stored proposals…</p>
        )}
      </div>
    </Page>
  );
}

function LiveReview({ review, spaceName }: { review: Review; spaceName: string | null }) {
  const state = useSyncExternalStore(review.subscribe, review.getState);
  return (
    <OntologyReviewView
      state={state}
      spaceName={spaceName}
      onMeasure={() => void review.measure()}
      onPress={(item, action) => void review.press(item, action)}
    />
  );
}
