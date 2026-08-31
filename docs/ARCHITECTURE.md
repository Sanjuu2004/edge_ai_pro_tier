# Edge AI Video Analytics Platform — Pro Tier Architecture

Jetson Orin NX, DeepStream-based. Three products, one shared framework:
PPE Industrial Safety (port 8103), Driver Monitoring (8104), Healthcare
Monitoring (8105).

## Directory layout

```
framework/       Reusable across all solutions. Never imports applications/.
applications/    Business logic per product (ppe_detection, driver_monitoring,
                 healthcare, + placeholders for industrial_safety, retail).
assets/          Models (.onnx/.engine), labels -- data, not code.
configs/         nvinfer configs + platform/solution YAML.
tests/           Unit tests (see Testing below).
scripts/         run_demo.sh, run_tests.sh.
backend/         apps/*/runtime_data/ (persisted DB/screenshots/uploads),
                 pipeline/ (StreamManager, VideoProcessor -- DeepStream
                 internals that don't belong in framework/ since they're
                 GStreamer-specific glue, not reusable abstractions),
                 platform_core/app_factory.py (assembles everything).
```

Each product is one process: `uvicorn applications.<name>.main:app --port <port>`.
`main.py` sets `BASE_DIR` to `backend/apps/pro_<name>/runtime_data/` --
deliberately decoupled from where the code lives, so runtime data survives
code reorganization.

## ModelManager -- live solution switching

**Problem:** a camera slot should be able to run a different business
solution (PPE -> Driver Monitoring) live, without restarting the process.

**Design:** `framework/model/model_manager.py` is a pure YAML-driven
registry. `configs/solutions/<name>.yaml` has `module`/`class` fields;
`ModelManager.load(name)` uses `importlib` to resolve them. This is what
lets framework/ have zero hardcoded knowledge of PPE/Driver/Healthcare --
it only knows how to read config and import whatever it names.

**The actual swap** (`StreamManager.bind_solution()`/`swap_solution()` in
`backend/pipeline/stream_manager.py`) turned out simpler than expected:
`StreamManager.start()` already tears down and rebuilds the entire
GStreamer pipeline from scratch on every call (including a fresh `nvinfer`
element). So swapping solutions live is just: stop() -> rebind Solution
instance + fresh EventManager (no debounce/alert-history bleed between
solutions) -> start() again on the same device. No need to reconfigure
`nvinfer` on a PLAYING pipeline, which would have been considerably riskier.

**API:** `POST /api/camera/{slot}/solution {solution_name}` --
`GET /api/camera/{slot}/manifest` for the slot's current branding/violation
types. `GET /api/solutions/manifest` (process-wide, boot-time) is
untouched/unaffected -- these are additive routes.

**Frontend gotcha this uncovered:** `vtype()` (theme.js) originally only
looked up violation types from `window.ACTIVE_MANIFEST` (the *process's*
boot-time manifest). After a live swap, an alert from a different
solution's violation type wouldn't be found there, silently falling back
to a generic warning icon. Fixed by having `app.js` fetch and merge
`violation_types` from *every* registered solution at boot
(`loadAllViolationTypes()`), with `vtype()` checking the merged table as
a fallback.

## CameraFactory -- USB + RTSP

`framework/camera/camera_factory.py` builds just the source-to-NVMM
segment of the pipeline; `StreamManager` links the result into
`streammux` exactly as before and continues unchanged from there.

**Why this isn't just "swap the source element":** USB's `v4l2src` has a
*static* pad -- link it synchronously like everything else. RTSP's
`rtspsrc` has a *dynamic* pad -- it only appears once `rtspsrc` actually
negotiates the stream over the network, so it must be linked via a
`pad-added` signal callback. `CameraFactory` owns that asynchrony so
`StreamManager` never has to special-case it.

- USB: `v4l2src -> capsfilter -> videoconvert -> nvvideoconvert -> NVMM caps`
  (needs CPU-side `videoconvert` since V4L2 doesn't deliver frames already
  in GPU memory).
- RTSP: `rtspsrc -(pad-added)-> rtph264depay -> h264parse -> nvv4l2decoder
  -> NVMM caps` (hardware decode straight into NVMM, no `videoconvert`
  needed). **H.264 only** -- no H.265 support yet.

`StreamManager.start(device_path, source_type="usb")` -- default keeps
every existing caller working unchanged. `POST /api/stream/{slot}/start`
accepts an optional `source_type` field (`"usb"` or `"rtsp"`), same
default.

**Verification status:** USB is regression-proven against the
pre-refactor pipeline on real hardware. RTSP is proven against a local
GStreamer-based RTSP test server (`gi.repository.GstRtspServer`, serving
`videotestsrc`) -- real H.264 negotiation, decode, and live browser
rendering at 30 FPS. **Not yet tested against a real physical RTSP
camera** (none available at time of writing) -- worth a sanity check
once one exists, since real cameras can differ in codec/auth/latency
behavior from a local test server.

## DataManager -- structured event logging

`framework/database/data_manager.py`, SQLite, one `events` table
(`camera_slot`, `solution`, `person_id`, `event_type`, `screenshot_path`,
`timestamp`, ...). Both `StreamManager` (live path) and `VideoProcessor`
(upload path) call `log_event()` right where `EventManager.save_screenshot()`
already runs, since every field needed is already a real Python value
there -- no new plumbing to compute them.

**Why this mattered:** `GET /api/screenshots` originally reverse-parsed
person_id/violation_type out of screenshot *filenames*
(`fname.split("_", 2)`). Driver Monitoring's person IDs contain
underscores (e.g. `driver_seatbelt`), which silently corrupted that
parse (`person_id` became `"driver"`, violation type became garbage).
Now `/api/screenshots` reads structured rows from `DataManager` directly
-- the bug is gone at the root, not patched around.

`camera_slot` convention: `"0"`/`"1"` for live slots, `"upload_<job_id>"`
(full ID, not truncated) for uploads -- lets the read side reconstruct
correct screenshot URLs with zero parsing.

## MQTT topic-per-solution

`MQTTPublisher` (one instance per process, shared across all slots) used
to publish every alert under one topic fixed at construction time. After
a live ModelManager swap, alerts from the new solution would still
publish under the *old* topic. Fixed by having `publish()` accept an
optional per-call `topic` override; `StreamManager`/`VideoProcessor` now
pass `topic=f"{self.solution.name}/alerts"` explicitly, computed from
whichever solution is currently bound -- correct after any number of
live swaps.

## Testing

`tests/` uses plain `unittest` (stdlib, no new dependencies).
`scripts/run_tests.sh` runs the full suite via `unittest discover`.

- `tests/model/` -- runs against the *real* `configs/solutions/*.yaml`
  (not mocked -- that data is stable, checked-in, and testing against
  the real files catches actual drift).
- `tests/database/` -- temp SQLite file per test, never touches real
  `platform.db`. Includes an explicit regression test for the
  `driver_seatbelt` underscore bug described above.
- `tests/camera/` -- deliberately thin. Full GStreamer pipeline
  behavior needs real device/network state that a unit test can't
  safely fake; only input validation is covered here. The real
  verification for USB/RTSP pipeline correctness is the manual
  hardware testing described above, not automated tests.

## Known remaining gaps

- CSI camera support (`nvarguscamerasrc`) -- not started.
- RTSP against real physical camera hardware -- untested (see above).
- `backend/pipeline/ppe_logic.py` is live (imported by
  `applications/ppe_detection/logic.py`); don't delete it despite the
  `pipeline/` naming similarity to already-removed dead files.
