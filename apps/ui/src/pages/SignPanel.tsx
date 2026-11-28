import { useEffect, useRef, useState, type PointerEvent } from "react";
import { SIGN_INTENT, strokesToSvg, type Stroke } from "@/lib/signCapture";

type Kind = "typed" | "drawn" | "upload";

/**
 * Shown only after GET /v1/sign/status returns ok. Flag off renders nothing,
 * so the audit page markup stays as it was.
 */
export function SignPanel({ enabled }: { enabled: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const strokes = useRef<Stroke[]>([]);
  const current = useRef<Stroke>([]);
  const [kind, setKind] = useState<Kind>("typed");
  const [typed, setTyped] = useState("");
  const [title, setTitle] = useState("Agreement");
  const [email, setEmail] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [note, setNote] = useState("");

  if (!enabled) return null;

  const pointer = (event: PointerEvent<HTMLCanvasElement>, down: boolean) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const point = {
      x: ((event.clientX - rect.left) / rect.width) * canvas.width,
      y: ((event.clientY - rect.top) / rect.height) * canvas.height,
    };
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (down) {
      current.current = [point];
      ctx.beginPath();
      ctx.moveTo(point.x, point.y);
      return;
    }
    current.current.push(point);
    ctx.lineTo(point.x, point.y);
    ctx.stroke();
  };

  const finishStroke = () => {
    if (current.current.length > 0) strokes.current.push(current.current);
    current.current = [];
  };

  const submit = async () => {
    if (!agreed) {
      setNote("Tick the consent line before signing.");
      return;
    }
    if (!email.includes("@")) {
      setNote("Enter the signer email.");
      return;
    }
    const canvas = canvasRef.current;
    const png =
      kind === "typed" || canvas == null ? "" : canvas.toDataURL("image/png").split(",")[1] || "";
    const svg = kind === "drawn" ? strokesToSvg(strokes.current, 320, 80) : "";
    if (kind === "typed" && typed.trim() === "") {
      setNote("Type the name you are signing with.");
      return;
    }
    if (kind !== "typed" && png === "") {
      setNote("Add a signature image first.");
      return;
    }
    const capture =
      kind === "typed"
        ? { kind, text: typed.trim(), png_b64: "", svg: "" }
        : { kind, text: "", png_b64: png, svg };
    const post = async (path: string, body: unknown) => {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = (await res.json().catch(() => ({}))) as { detail?: string };
      if (!res.ok) throw new Error(payload.detail || "sign_failed");
      return payload as Record<string, string>;
    };
    try {
      const created = await post("/api/v1/sign/envelopes", {
        title,
        body: "",
        fields: {},
        order: "parallel",
      });
      const signer = await post(`/api/v1/sign/envelopes/${created.envelope_id}/signers`, {
        email,
        auth_method: "email_otp",
        identity: "",
        position: 1,
        ttl_s: 3600,
      });
      const signed = await post(`/api/v1/sign/envelopes/${created.envelope_id}/sign`, {
        signer_id: signer.signer_id,
        token: signer.token,
        otp: signer.otp,
        consent_text: SIGN_INTENT,
        intent: SIGN_INTENT,
        capture,
      });
      setNote(`Signed. sha256 ${signed.sha256_after}`);
    } catch (err) {
      setNote(err instanceof Error ? err.message : "sign_failed");
    }
  };

  return (
    <section className="mt-8 max-w-xl" data-testid="sign-panel">
      <h2 className="font-[family-name:var(--font-display)] text-xl font-semibold">Sign</h2>
      <label className="mt-4 block text-sm">
        Title
        <input
          className="mt-1 w-full border border-[var(--color-line)] px-3 py-2"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          aria-label="Document title"
        />
      </label>
      <label className="mt-3 block text-sm">
        Email
        <input
          className="mt-1 w-full border border-[var(--color-line)] px-3 py-2"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          aria-label="Signer email"
        />
      </label>
      <p className="mt-2 text-sm text-[var(--color-ink-muted)]">
        Type a name, draw, or upload an image. Signing happens on the server with your consent.
      </p>
      <div className="mt-4 flex gap-2 text-sm">
        {(["typed", "drawn", "upload"] as Kind[]).map((item) => (
          <button
            key={item}
            type="button"
            className="border border-[var(--color-line)] px-3 py-1"
            onClick={() => setKind(item)}
          >
            {item}
          </button>
        ))}
      </div>
      {kind === "typed" ? (
        <input
          className="mt-4 w-full border border-[var(--color-line)] px-3 py-2 font-serif text-2xl italic"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          placeholder="Your name"
          aria-label="Typed signature"
        />
      ) : null}
      {kind === "drawn" ? (
        <canvas
          ref={canvasRef}
          width={320}
          height={80}
          className="mt-4 w-full border border-[var(--color-line)] bg-white"
          aria-label="Draw a signature"
          onPointerDown={(event) => pointer(event, true)}
          onPointerMove={(event) => {
            if (event.buttons === 1) pointer(event, false);
          }}
          onPointerUp={finishStroke}
        />
      ) : null}
      {kind === "upload" ? (
        <input
          className="mt-4 block text-sm"
          type="file"
          accept="image/png"
          aria-label="Upload a signature image"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = () => {
              const canvas = canvasRef.current;
              if (!canvas || typeof reader.result !== "string") return;
              const image = new Image();
              image.onload = () => {
                const ctx = canvas.getContext("2d");
                if (!ctx) return;
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
              };
              image.src = reader.result;
            };
            reader.readAsDataURL(file);
          }}
        />
      ) : null}
      {kind === "upload" ? <canvas ref={canvasRef} width={320} height={80} className="hidden" /> : null}
      <label className="mt-4 flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          checked={agreed}
          onChange={(event) => setAgreed(event.target.checked)}
        />
        <span>{SIGN_INTENT}</span>
      </label>
      <button
        type="button"
        className="mt-4 border border-[var(--color-line)] px-3 py-2 text-sm"
        onClick={() => void submit()}
      >
        Sign
      </button>
      {note ? <p className="mt-3 text-sm">{note}</p> : null}
    </section>
  );
}

export function SignGate() {
  const [on, setOn] = useState(false);
  useEffect(() => {
    void fetch("/api/v1/sign/status")
      .then((res) => {
        setOn(res.ok);
      })
      .catch(() => setOn(false));
  }, []);
  return <SignPanel enabled={on} />;
}
