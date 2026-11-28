import { useSyncExternalStore } from "react";

export type FeatureMap = Record<string, boolean>;

/**
 * SPA paths that exist only while a feature flag is the digit 1.
 * One map, so a nav link and the route agree.
 */
const OPTIONAL_PATHS: Record<string, string> = {
  "/insights": "insight_proof",
};

type Listener = () => void;

let current: FeatureMap | null = null;
const listeners = new Set<Listener>();

export function publishFeatures(next: FeatureMap): void {
  current = next;
  for (const listener of listeners) listener();
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function snapshot(): FeatureMap | null {
  return current;
}

export function useFeatures(): FeatureMap | null {
  return useSyncExternalStore(subscribe, snapshot, () => null);
}

/** True only after the features read has landed and that flag is on. */
export function useFeature(name: string): boolean {
  const features = useFeatures();
  return features?.[name] === true;
}

/** The flag a path needs, or null when the path is always available. */
export function featureForPath(path: string): string | null {
  return OPTIONAL_PATHS[path] ?? null;
}
