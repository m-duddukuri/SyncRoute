# SyncRoute — Hybrid Routing App (MVP)

A working local prototype of synchronized multi-person itinerary planning with a deterministic OR-Tools CP-SAT optimizer and optional OpenAI-powered planning assistant.

## Run

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

Open the localhost URL Streamlit prints. Optionally enter an OpenAI API key in the sidebar to enable tool-calling chat. Without a key, basic local chat commands work.

## Test

```bash
python -m pytest -q
```

## What works

- Multiple travelers and individual/shared visits
- Shared visits start at the same time, with waiting permitted
- Time windows, visit durations, required and optional stops
- Minimize combined travel and waiting time while prioritizing optional visits
- Constraint validation independent of solver
- Editable stops, JSON import/export, per-traveler timelines
- Optional agent tools: optimize, change trip deadline, edit stop timing

## MVP limitations

- **Synthetic travel-time matrix**. Not connected to Google Maps or live traffic.
- **Single shared depot**. Different starting/ending locations are not yet supported.
- Import complete JSON to add/remove stops because the travel-time matrix must be resized consistently.
- Optimization can time out on difficult instances; a timeout is not proof of infeasibility.
- Chat uses an API key provided at runtime and incurs provider charges if enabled.
- Not hardened for production use or public deployment.
- The objective is *not* strictly lexicographic: optional stops are prioritized using a bounded penalty, then driving plus waiting are minimized.

## Data format

See `sample_trip.json`. Times are integer minutes after midnight (13:00 = 780). Matrix index 0 is the common depot; indices 1..N correspond to `stops` in order. All times are local, same-day times.

## Next steps

1. Different start and end depots for each traveler.
2. Geocoding and travel-time matrix via a mapping provider.
3. Add/remove stops in the UI with matrix recomputation.
4. Better infeasibility diagnostics and scalable optimization benchmarks.
5. Authentication, saved trips, and real-time collaboration after validation.
