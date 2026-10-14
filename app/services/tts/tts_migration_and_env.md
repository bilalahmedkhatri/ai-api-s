# 1) Alembic migration (TTSJob)

```python
def upgrade():
    op.add_column("tts_jobs", sa.Column("duration_sec", sa.Float(), nullable=True))
    op.add_column("tts_jobs", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("tts_jobs", sa.Column("object_key", sa.String(512), nullable=True))
    op.add_column("tts_jobs", sa.Column("error_message", sa.String(500), nullable=True))
    op.create_index("ix_tts_jobs_status_updated", "tts_jobs", ["status", "updated_at"])

def downgrade():
    op.drop_index("ix_tts_jobs_status_updated", "tts_jobs")
    for c in ("error_message", "object_key", "size_bytes", "duration_sec"):
        op.drop_column("tts_jobs", c)
```

Same columns on the `TTSJob` model:
```python
duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
```

# 2) .env / settings (all optional)
```
FRONTEND_WEBHOOK_URL=https://your-frontend.com/api/tts/webhook
FRONTEND_WEBHOOK_SECRET=change-me
TTS_MAX_CHARS=20000
KOKORO_MAX_CONCURRENCY=2
```
Add the same names (lowercase) to `Settings` in app/core/config.py.

# 3) Next.js webhook receiver (frontend server)
```ts
// app/api/tts/webhook/route.ts
import crypto from "crypto";
import { NextResponse } from "next/server";

export async function POST(req: Request) {
  const raw = await req.text();
  const sig = req.headers.get("x-tts-signature") ?? "";
  const expected =
    "sha256=" + crypto.createHmac("sha256", process.env.TTS_WEBHOOK_SECRET!).update(raw).digest("hex");

  const ok =
    sig.length === expected.length &&
    crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected));
  if (!ok) return NextResponse.json({ error: "bad signature" }, { status: 401 });

  const e = JSON.parse(raw);
  // e.job_id, e.status, e.audio_url, e.duration_sec, e.size_bytes, e.size_mb ...
  // TODO: save to your DB (upsert by job_id so duplicate deliveries are harmless)
  return NextResponse.json({ received: true });
}
```
