# TTS final phase: manual testing

13 October 2026. Code ki local verification aur real provider/B2 verification ko alag rakhein. Is phase mein schema nahin badla; previous migration head `f53c9e012d74` hi chahiye. Frontend users aur FastAPI owners separate hain.

## Start karne se pehle

- Gateway aur frontend ko updated source ke saath restart karein. Gateway par **one worker** rakhein. Default leases 1200 seconds hain, heartbeat 60 seconds, recovery scan 30 seconds.
- Existing app credentials, Google sessions, active TTS model, Gemini provider keys aur B2 settings reuse honge. Kisi secret ko chat/browser/URL mein paste na karein.
- Different-key distribution dekhne ke liye kam az kam 2 active, usable Gemini keys chahiye. Sirf 1 key par bhi default 2 calls overlap kar sakti hain. `TTS_GEMINI_KEY_CONCURRENCY=1` se strict serialization milti hai; overall `TTS_GEMINI_CONCURRENCY` aur request-start pacing bhi apply hoti hai. DB pool na ho to distinct `GEMINI_API_KEYS` use hoti hain. Key selection logs mein sirf provider aur key ID aati hai; provider response/chunk completion mein elapsed seconds hain. Keys additional provider/project quota create nahin kartin.
- Docker compose already `./logs:/app/logs` mount aur `WORKERS=1` rakhta hai. Hosted deployment mein logs directory persistent honi chahiye aur streaming response buffering disabled honi chahiye. Docker context ab `.env`, `.venv`, logs aur local DB exclude karta hai; credentials runtime environment se dein. Docker image execution is phase mein verify nahin hui.

Backend directory se:

```powershell
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

Frontend `D:\voice_generator_free\free_voice_generator` se:

```powershell
npm run dev
```

Real local test ke liye `http://localhost:3000` use karein, kyun ke ye registered app origin hai. Port 3011 sirf mocked browser verification mein use hua tha.

## Check sequence

1. **Generation aur voice:** Apne plan ki allowed length mein 3 separate paragraphs likhein. Gemini/Kore ya apni selected voice choose karein. Ek generation start karein. Terminal mein indexed chunks aur provider key IDs dekhein; secrets/script text log nahin hona chahiye.
2. **Live sequence:** **Listen live** click karein. Parts line se play hon; missing part ke aage wala ready part wait kare. Final audio bhi play/download ho. Generative expression/prosody vary kar sakti hai, lekin selected voice configuration change nahin honi chahiye.
3. **Refresh aur navigation:** Processing ke darmiyan refresh karein, ya Audio Library se **Resume in Studio** use karein. DevTools mein `/api/voiceover` dobara POST nahin honi chahiye; sirf status/stream ya explicit retry calls hon. Credits initial generation par hi charge hon. Complete hone par pending entry remove ho.
4. **Retry:** Test-provider failure ko isolated suite mein simulate kiya gaya hai. Agar real job fail ho, reason/quota pehle check karke **Retry unfinished audio** karein. Successful chunk attempts same rahen; sirf failed/missing parts regenerate hon. Content-detail mein missing audio par wohi progress/retry available hai. Production credentials/quota ko jaan-boojh kar damage na karein.
5. **Restart recovery:** Sirf local disposable test mein processing interrupt karke gateway restart karein. Old worker ki lease expire hone ke baad maintenance naya token claim kare; completed prefix regenerate na ho. Shorter local test ke liye launch se pehle `$env:TTS_LEASE_SECONDS='180'` aur `$env:TTS_RECOVERY_INTERVAL='5'` rakh sakte hain; baad mein ye overrides remove karein. Healthy leases, denied apps/owners aur failed/quota jobs auto-retry nahin hoti.
6. **B2 cleanup:** Final object/save aur completed status confirm hone ke baad referenced chunk objects delete hon. Orphan cleanup hourly sirf 24-hour-old unreferenced temporary WAVs dekhti hai; active runs aur incomplete jobs ke checkpoints protected hain. Final audio prefix sweep nahin hota. Ye safety/pagination cases fake B2 par tested hain; real bucket mein lifecycle policy enable karna zaroori nahin hai.
7. **JSONL archival:** 1000 records ke baad batch ka naam first/last UTC date aur hash ke saath badle. B2 `logs/YYYY/MM/` mein upload confirm karein. Upload failure par local pending file retained rahe; clean shutdown partial batch bhi seal karta hai. Log upload/delete tests fake storage par hue hain; real bucket result ab check karna hai.
8. **Account isolation:** Doosre frontend account mein pehle account ki pending job/history na dikhni chahiye. Foreign job ID status/retry/stream par denied ho. Guest jobs ko matching cookie aur signed proof chahiye.

## Automated checks

Previous recovery phase: 115 backend tests + 48 frontend ownership tests passed, including stream reconnect and content-detail retry. The 2026-10-14 correction passed **117 backend tests + 50 frontend ownership tests**, changed-flow Ruff/ESLint/typecheck and production frontend build. Edge fixtures verified completed Library rows, pending removal, automatic completion refresh, visible same-account focus, offline recovery/history, account isolation and zero page errors, with zero new generation requests. A read-only query against the configured frontend database returned 28 completed Studio voices for the screenshot account; the actual list SQL and single play/download URL lookup succeeded. No live Gemini synthesis or B2 transfer was performed. Earlier unrelated page/AudioPlayer lint baseline is not reported as a whole-repository lint pass.

## Library aur timing regression check

Backend aur frontend restart karein. Studio voice complete hone ke baad Library mein usi user ka row dikhna chahiye; linked template ki duplicate row nahin. Processing ke darmiyan Library par rahen: completion ke 15 seconds ke andar unfinished entry hatni chahiye aur table refresh hona chahiye. Same-user tab focus par editor/workspace visible rahna chahiye; actual account change par identity reload ab bhi hota hai. Logs mein overlapping chunk starts aur provider response elapsed times compare karein. Live preview index order generation concurrency se alag hai, is liye sunne ke liye parts hamesha order mein chalengi. Real Gemini duration, quota limits aur network/storage time ki wajah se fixed generation-time guarantee nahin hai. Nayi migration nahin chahiye.

Backend tests SQLite aur fake providers/B2/webhooks use karte hain; production DB/provider calls nahin karte:

```powershell
$env:LITELLM_LOCAL_MODEL_COST_MAP='True'
.venv\Scripts\python.exe -m pytest tests/test_tts_recovery.py tests/test_tts_stream_migration.py tests/test_tts_streaming.py tests/test_log_batches.py tests/test_tts_chunks.py tests/test_tts_unified.py tests/test_tts_audio.py tests/test_app_authorization.py tests/test_bootstrap_genzee_apps.py -q -p no:cacheprovider
```

Frontend:

```powershell
node --test tests/ownership.test.cjs
npx tsc --noEmit
npm run build
```

50,000-character/20-chunk test fake one-second WAVs ke saath concurrency, ordering aur cleanup verify karta hai. Isay real audio memory/latency benchmark na samjhein. Final merge abhi memory mein hoti hai; shared multi-worker/project limits aur disk merge future scaling work hain. Local tests hosted buffering, actual quota, Google authentication ya real B2 writes/deletes certify nahin karte.
