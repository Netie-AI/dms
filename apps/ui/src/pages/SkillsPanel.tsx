import { useEffect, useMemo, useState } from "react";

type SkillRow = {
  name: string;
  description: string;
  licence?: string;
};

export function SkillsPanel({
  enabled,
  skills,
  googleMessage,
  query,
  onQuery,
}: {
  enabled: boolean;
  skills: SkillRow[];
  googleMessage: string;
  query: string;
  onQuery: (value: string) => void;
}) {
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return skills;
    return skills.filter((skill) =>
      `${skill.name} ${skill.description}`.toLowerCase().includes(needle),
    );
  }, [query, skills]);
  if (!enabled) return null;
  return (
    <section
      data-testid="skills-panel"
      className="mt-6 border border-[var(--color-line)] bg-[var(--color-surface)]/70 px-4 py-4"
    >
      <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
        Skills
      </p>
      <p className="mt-2 text-sm text-[var(--color-ink)]">
        Browse and install skills. The provider runs them. DMS records the grant and the audit.
      </p>
      <p className="mt-2 text-sm" data-testid="skills-google">
        {googleMessage}
      </p>
      <input
        data-testid="skills-search"
        value={query}
        onChange={(event) => onQuery(event.target.value)}
        placeholder="Search skills"
        className="mt-3 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-2 text-sm"
      />
      <ul className="mt-3" data-testid="skills-list">
        {shown.map((skill) => (
          <li key={skill.name} className="mt-2 text-sm" data-testid={`skill-${skill.name}`}>
            <span className="font-medium">{skill.name}</span>
            <span className="text-[var(--color-ink-muted)]"> {skill.description}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function SkillsGate() {
  const [enabled, setEnabled] = useState(false);
  const [skills, setSkills] = useState<SkillRow[]>([]);
  const [googleMessage, setGoogleMessage] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    let alive = true;
    void fetch("/api/v1/studio/skills/status")
      .then(async (res) => {
        if (!res.ok || !alive) return;
        const catalog = await fetch("/api/v1/studio/skills/catalog");
        const google = await fetch("/api/v1/studio/skills/google");
        if (!alive) return;
        const catalogBody = catalog.ok ? await catalog.json() : { skills: [] };
        const googleBody = google.ok ? await google.json() : { message: "" };
        setSkills(Array.isArray(catalogBody.skills) ? catalogBody.skills : []);
        setGoogleMessage(typeof googleBody.message === "string" ? googleBody.message : "");
        setEnabled(true);
      })
      .catch(() => setEnabled(false));
    return () => {
      alive = false;
    };
  }, []);
  return (
    <SkillsPanel
      enabled={enabled}
      skills={skills}
      googleMessage={googleMessage}
      query={query}
      onQuery={setQuery}
    />
  );
}
