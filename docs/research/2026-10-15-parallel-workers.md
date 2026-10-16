# Several users at once: parallel processing in the app (analysis)

**Date:** 15 October 2026 · **Status:** analysed, then A, B and C implemented the same day (section 7) · **Question:** today a user waits until the job before theirs is finished; how could the demo serve several users at the same time?

## 1. What the server does today

One worker thread takes every piece of work from one queue, in order: the analysis of an upload (layout, scale cues), the scale cues of a changed area, the extraction, and the read-ahead of the drawing text (lowest priority). The pipeline runs inside the server process: the segmenter (98 MB, torch, all cores) and the OCR engine (RapidOCR on ONNX) are loaded once at start. The public server refuses new work above 10 jobs waiting or running, drops queued work nobody asked about for 3 minutes, and keeps a sheet for 60 minutes after its last request.

Measured on 15 October with the demo sheet (cp-usace-bde-hq-1f, working image 3225 × 1928 px):

| Step | Server (8 vCPU, 23 GB) | Laptop (4 torch threads) |
|---|---|---|
| Analysis: layout + scale cues | 9.6 s (3.3 + 6.3) | about 12 s |
| Extraction | 29.5 s | 34.6 s |
| of which segmentation (torch, all cores) | 18.0 s (61 %) | 23.7 s |
| of which Python stages (walls, openings, rooms, derived, export) | 8.2 s | 8.3 s |
| Service memory at rest, after the warm-up | 0.59 GB | 0.64 GB |
| Peak during the job | 1.81 GB (+1.2 GB) | 2.36 GB (+1.7 GB) |
| An analysed session kept in memory (rasters; 3 are kept) | about 1.2 GB each | 1.2 GB |

Limits that frame the options: `MemoryHigh` 15.6 GB and `MemoryMax` 18.0 GB for the service, `CPUWeight` 50, nginx `proxy_read_timeout` 300 s, 14 GB free disk.

**What a second user sees.** Every request joins the same queue, so a newcomer's upload is analysed only after the running extraction (up to 30 s) and anything queued before it; the same for their scale cues and their own extraction. With three users in the flow, each step can wait behind two extractions, about a minute, and the page says only "waiting for the worker", without a position or a time.

## 2. What limits parallelism on this machine

| Limit | Effect |
|---|---|
| CPU: the segmentation already uses all 8 cores | Two extractions at once share the cores; each takes about 1.7 × as long (torch scales sub-linearly, so two jobs on 4 threads each finish slightly sooner than one after the other: +15–25 % throughput, no more). The gain of parallel workers is fairness and overlap, not speed. |
| The GIL | The Python stages (8 s per job) and the layout detection run one at a time in threads; the OCR and torch calls release it. Processes have no such limit. |
| Memory | Each worker process needs its own model and engine (0.6 GB) plus the job's peak (1.2 GB) and, for the light steps, the analysed session (1.2 GB). Three extraction processes with their jobs need about 6 GB, well under the 15.6 GB `MemoryHigh`. |
| Shared state in the server | `JOBS` with one lock, one `CURRENT` record (the status endpoint and the ETA), the in-memory `SESSIONS` (bounded), the queue, the read-ahead bookkeeping and the sweeper. The bridge holds the model and the engine under one lock; the pipeline keeps no module-level mutable state (checked: only a compiled regex), so it can run in several threads or processes. |
| Sessions | The analysed session (packages with their rasters) lives in the server process; `session.pkl` on disk exists for the local server. A worker process would read it from disk, so the public server would have to write it too. |

## 3. Options

| | Option | A newcomer while an extraction runs | Throughput | Memory | Effort | Risk |
|---|---|---|---|---|---|---|
| A | **Queue position and ETA** on the page ("1 Plan vor Ihnen, etwa 1 min") | waits as today, but knows why and how long | same | same | 2–3 h | none |
| B | **Two lanes**: a light worker thread (analysis, scale cues) and a heavy one (extraction, read-ahead) | gets layout and scale cues in about 10 s instead of up to 40 s; the extraction still queues | same, plus the light work overlaps the heavy | +1.2 GB while both run | half a day | the light lane slows a running extraction by 10–20 % while it works; the OCR thread pool should be capped at 2–3 threads so it never takes the cores from the segmentation |
| C | **N extraction worker processes** (2–3), each with its own model, fed by the server | two or three extractions progress at once, each about 1.7 × slower than alone | +15–25 % | 0.6 GB per process + 1.2 GB per running job: 3 workers ≈ 6 GB | 2–3 days | process plumbing (progress, log and result back to the server; sessions through `session.pkl`; restart of a dead worker). A crash in the pipeline no longer takes the server down, which is a gain. |
| D | **A shorter job**: segmentation without flip averaging, fewer tiles, smaller work image | less waiting for everyone | up to +30 % | same | done where it was free (read-ahead); the rest was measured and rejected for quality (wall IoU 0.781 → 0.750 without flip averaging) | quality |
| E | **More machines**: a second VPS behind nginx, or a GPU box | one worker per machine; on a small GPU the segmentation drops from 18 s to about 1 s | × machines | — | days, plus running cost | operations: sticky routing or shared storage for a job's files |

## 4. Recommendation

1. **A and B first** (one day together). They remove the two things a second user notices today: the long silent wait before the first screen, and the wait without explanation. Everything else stays as it is: one extraction at a time, one process, no new memory.
2. **C when the demo has real concurrent use**, with two worker processes. The server keeps the job records and the API; the workers do only extraction. Memory fits with room, and the pipeline needs no change.
3. **E only when the load needs more than two or three extractions a minute.** A GPU makes a single worker fast enough that parallel workers matter less.

## 5. Sketch of B (two lanes)

- `app/server.py`: one priority queue per lane, `light` (analysis, scale) and `heavy` (extract, read-ahead at low priority); two `worker(lane)` threads; `CURRENT` per lane; `/api/status` reports both; `check_capacity` counts per lane (the 10 waiting-or-running stays for the heavy lane).
- Thread budget: torch keeps all cores; the OCR engine's ONNX session gets `intra_op_num_threads` 2–3 (RapidOCR takes it in its params); OpenCV stays at 4.
- Page: the "waiting for the worker" stage gets the number of heavy jobs ahead and an ETA from the stage durations of `progress.js`; the server keeps a mirror list of the queue for that count.
- Tests: an analysis runs to "ready" while the heavy lane is held by a gate (the stub bridge already has a gate for this pattern).

## 6. Sketch of C (worker processes)

- `multiprocessing` with `spawn`; each child imports the bridge, warms the model and the engine, and loops over a job queue; progress and log lines go back on a queue that a server thread writes into the job record with `commit` and `log`; the result is the `out` folder on disk, as today.
- The analysed session is written as `session.pkl` after the analysis (the public server does not write it today) and read by the worker; the in-memory `SESSIONS` stays for the light steps in the server.
- Memory budget against `MemoryHigh` 15.6 GB: server 0.6 GB + N × (0.6 + 1.2) GB → N = 2: 4.2 GB, N = 3: 6.0 GB, N = 4: 7.8 GB. Choose N = 2 on 8 cores; more workers only share the same cores.
- A worker that dies is restarted by the server; the job it held is marked as failed with the reason.

## 7. What was built (15 October)

| Piece | Where | What |
|---|---|---|
| Two lanes | `app/server.py` (`LANES`, `LANE_OF`, `worker(lane, name)`) | The light lane (analysis, scale step) on a thread of the server; the heavy lane (extraction, read-ahead at low priority) on `FPX_APP_WORKERS` worker processes, or one thread with 0. An extraction waits while another worker still reads its sheet's text ahead. |
| Worker processes | `app/worker.py`, `server.Remote`, `server.pump` | One thread per process in the server; the process gets the job folder, the confirmations and the analysis on a pipe, reads the analysed session from `session.pkl` (written after the analysis, also on the public server), sends log lines and progress back as they happen, then the result. A process that dies is replaced; the job it held fails with the reason. The segmenter's threads: all cores while one worker is busy, a share with two. |
| Queue position and wait | `server.queue_eta`, `queue_info`, the sheet poll; `ui.js progressBar`, `state.js` | The poll of a waiting job carries `progress.position` and `progress.wait` (also with the unchanged reply, so the wait moves between revisions); the estimate takes the busy workers (what is left of a typical run, the last five averaged per kind) and the queue in order. The page says "n Blätter vor Ihnen in der Warteschlange…" with the time left. |
| Status | `GET /api/status` | Queued per lane, the workers, what each does. |
| Deploy | `app/deploy/setup.sh`, `fpx-app.service` | `FPX_APP_WORKERS=2` in `/etc/fpx-app.env`; `OMP_WAIT_POLICY=PASSIVE` so two segmentations sharing the cores do not spin. |
| Tests | `app/tests/test_lanes.py`, `stubbridge.py`, `test_browser.py` | The light lane runs while the heavy one is held; the position and wait of a queued job and the estimates; an extraction in a worker process (the stub bridge loaded from a file) and a process that dies mid-job; the queue text on the page. |
