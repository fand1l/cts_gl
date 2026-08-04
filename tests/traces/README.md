# Recorded cursor traces

Every file here is replayed through the real detector by
`node tests/test_detection.js`, and has to end the way it says it should. A
misfire that was reported once cannot come back unnoticed.

A trace holds nothing but pointer coordinates and timestamps.

## Format

```json
{
 "expect": "no-fire",
 "description": "dragging a window diagonally back and forth",
 "settings": { "minSpeedPxPerSec": 400 },
 "samples": [ { "x": 1000, "y": 500, "t": 0 } ],
 "notes": []
}
```

* `expect` — `"no-fire"` (the overlay must **not** open) or `"fire"` (it must,
  exactly once). Both directions matter: tightening a threshold until nothing
  misfires is easy, and useless if real shakes stop working too.
* `settings` — optional overrides for that one trace, using the same keys as
  `[Script-circletosearch]` in `kwinrc`. Omit it to test against the defaults.
* `samples` — `t` in milliseconds, relative to the first sample.
* `notes` — whatever the recorder logged; ignored by the tests.

## Adding one

Two ways, both producing this exact format:

```bash
./tools/record-trace.sh my-misfire.json    # redo the movement, then Ctrl-C
cp my-misfire.json tests/traces/
```

or answer **"No, that was accidental"** when the daemon asks whether a trigger
was wanted — it writes the movement to
`~/.local/share/circle-to-search/traces/` by itself. Copy the file in and set
`expect` (the daemon already fills it in from your answer).

Then check what the detector currently does with it, and what a settings change
would do:

```bash
node tests/replay-trace.js tests/traces/my-misfire.json
node tests/replay-trace.js tests/traces/my-misfire.json minSpeedPxPerSec=1000
node tests/test_detection.js
```

## The seeds

The files shipped here are marked *synthetic seed* in their `description`: they
are the movements that used to misfire, written out from the generators in
`test_detection.js` so that the replay path is covered from the very first
commit. Real recordings are worth more than all of them — please add yours.
