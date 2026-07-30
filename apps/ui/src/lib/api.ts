/** DMS API client — browser talks only to DMS (proxied /api). Never Cortex. */

export type HealthBody = {
  status: string;
  product?: string;
};

export async function fetchHealth(signal?: AbortSignal): Promise<HealthBody | null> {
  try {
    const res = await fetch("/api/health", { signal });
    if (!res.ok) return null;
    return (await res.json()) as HealthBody;
  } catch {
    return null;
  }
}
