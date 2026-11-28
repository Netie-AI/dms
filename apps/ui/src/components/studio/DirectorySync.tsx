import { useEffect, useState } from "react";

export type DirectoryMap = {
  id: string;
  kind: "department" | "group";
  source: string;
  priority: number;
  role: string;
  gov_role: string;
  spaces: string[];
  daily_tokens: number | null;
};

export type DirectoryForm = {
  kind: string;
  source: string;
  role: string;
  gov_role: string;
  spaces: string;
  daily_tokens: string;
  priority: string;
};

export const emptyDirectoryForm: DirectoryForm = {
  kind: "department",
  source: "",
  role: "viewer",
  gov_role: "member",
  spaces: "",
  daily_tokens: "",
  priority: "100",
};

/** Presentational directory panel. The live wrapper fetches and saves. */
export function DirectorySyncView({
  maps,
  form,
  issued,
  note,
  onForm,
  onSave,
  onRotate,
}: {
  maps: DirectoryMap[];
  form: DirectoryForm;
  issued?: string | null;
  note?: string | null;
  onForm?: (next: DirectoryForm) => void;
  onSave?: () => void;
  onRotate?: () => void;
}) {
  const set = (patch: Partial<DirectoryForm>) => onForm?.({ ...form, ...patch });
  return (
    <section data-testid="directory-sync" className="mt-8 border border-[var(--color-line)] p-4">
      <h3 className="text-sm font-medium">Directory sync</h3>
      <p className="mt-1 max-w-2xl text-sm text-[var(--color-ink-muted)]">
        A department or a group name maps to a role, a budget, and space grants. The identity
        provider sends the name. This list is the only mapping.
      </p>
      <ul className="mt-3 space-y-2 text-sm">
        {maps.length === 0 && (
          <li className="text-[var(--color-ink-muted)]">No directory mappings yet.</li>
        )}
        {maps.map((row) => (
          <li key={row.id} data-testid="directory-map">
            {row.kind} {row.source} maps to {row.role}
            {row.spaces.length ? ` (${row.spaces.length} spaces)` : ""}
          </li>
        ))}
      </ul>
      <form
        className="mt-4 grid gap-2 sm:grid-cols-2"
        data-testid="directory-form"
        onSubmit={(event) => {
          event.preventDefault();
          onSave?.();
        }}
      >
        <select
          aria-label="Kind"
          value={form.kind}
          onChange={(event) => set({ kind: event.target.value })}
          className="border border-[var(--color-line)] px-2 py-1.5 text-sm"
        >
          <option value="department">Department</option>
          <option value="group">Group</option>
        </select>
        <input
          aria-label="Source name"
          value={form.source}
          placeholder="Department or group name"
          onChange={(event) => set({ source: event.target.value })}
          className="border border-[var(--color-line)] px-2 py-1.5 text-sm"
        />
        <select
          aria-label="Directory role"
          value={form.role}
          onChange={(event) => set({ role: event.target.value })}
          className="border border-[var(--color-line)] px-2 py-1.5 text-sm"
        >
          <option value="viewer">viewer</option>
          <option value="analyst">analyst</option>
          <option value="steward">steward</option>
          <option value="admin">admin</option>
        </select>
        <select
          aria-label="Governance role"
          value={form.gov_role}
          onChange={(event) => set({ gov_role: event.target.value })}
          className="border border-[var(--color-line)] px-2 py-1.5 text-sm"
        >
          <option value="member">member</option>
          <option value="manager">manager</option>
        </select>
        <input
          aria-label="Space ids"
          value={form.spaces}
          placeholder="Space ids, comma separated"
          onChange={(event) => set({ spaces: event.target.value })}
          className="border border-[var(--color-line)] px-2 py-1.5 text-sm"
        />
        <input
          aria-label="Daily tokens"
          value={form.daily_tokens}
          placeholder="Daily tokens"
          onChange={(event) => set({ daily_tokens: event.target.value })}
          className="border border-[var(--color-line)] px-2 py-1.5 text-sm"
        />
        <div className="flex gap-2">
          <button
            type="submit"
            data-testid="directory-save"
            className="border border-[var(--color-line)] px-3 py-1.5 text-sm"
          >
            Save mapping
          </button>
          <button
            type="button"
            data-testid="directory-token"
            onClick={() => onRotate?.()}
            className="border border-[var(--color-line)] px-3 py-1.5 text-sm"
          >
            Issue token
          </button>
        </div>
      </form>
      {issued ? (
        <label className="mt-3 block text-sm">
          Provisioning token, shown once
          <input
            aria-label="Provisioning token"
            readOnly
            value={issued}
            className="mt-1 w-full border border-[var(--color-line)] px-2 py-1.5 font-mono text-xs"
          />
        </label>
      ) : null}
      {note ? <p className="mt-2 text-sm text-[var(--color-ink-muted)]">{note}</p> : null}
    </section>
  );
}

/** Fetches directory mappings. Renders nothing until the flag-on route answers. */
export function DirectorySync() {
  const [maps, setMaps] = useState<DirectoryMap[] | null>(null);
  const [form, setForm] = useState<DirectoryForm>(emptyDirectoryForm);
  const [issued, setIssued] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = () => {
    void fetch("/api/v1/scim/admin/maps")
      .then(async (res) => (res.ok ? ((await res.json()) as { maps: DirectoryMap[] }) : null))
      .then((body) => setMaps(body ? body.maps : null))
      .catch(() => setMaps(null));
  };

  useEffect(() => {
    load();
  }, []);

  if (!maps) return null;

  const save = async () => {
    setNote(null);
    const spaces = form.spaces
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    const tokens = form.daily_tokens.trim() === "" ? null : Number(form.daily_tokens);
    const res = await fetch("/api/v1/scim/admin/maps", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        kind: form.kind,
        source: form.source.trim(),
        directory_role: form.role,
        gov_role: form.gov_role,
        priority: Number(form.priority || "100"),
        spaces,
        daily_tokens: tokens,
        ask: true,
        create: true,
        connectors: true,
      }),
    });
    setNote(res.ok ? "Mapping saved." : "Mapping was not saved.");
    if (res.ok) load();
  };

  const rotate = async () => {
    setIssued(null);
    setNote(null);
    const res = await fetch("/api/v1/scim/admin/token", { method: "POST" });
    const body = (await res.json().catch(() => null)) as { token?: string } | null;
    if (!res.ok || !body?.token) {
      setNote("Token was not issued.");
      return;
    }
    setIssued(body.token);
    setNote("Paste this token into the identity provider. It is not shown again.");
  };

  return (
    <DirectorySyncView
      maps={maps}
      form={form}
      issued={issued}
      note={note}
      onForm={setForm}
      onSave={() => void save()}
      onRotate={() => void rotate()}
    />
  );
}
