# TASKS — SidelineIQ Data Layer

You own the perception layer in your architecture: **Soccer API → Data cleaner → features → Tactical synthesis agent**. I built the scaffolding. These are the concrete tasks to finish it. Tackle in order; each builds on the last.

## What's already there for you

```
prophet/sports/soccer/
  schema.py       ← Canonical event/match types (don't change without discussion)
  events.py       ← Multi-source fetchers (ESPN, football-data, mock)
  normalizer.py   ← Source → canonical schema
  features.py     ← Feature engineering: MatchSnapshot the synthesis agent reads
  demo.py         ← End-to-end CLI you can run RIGHT NOW
data/games/
  mock_LIV_ARS_2026.json   ← Bundled mock match for offline dev
```

**Smoke-test it:**
```bash
python3 -m prophet.sports.soccer.demo --mock
python3 -m prophet.sports.soccer.demo --mock --stream --stride 15
```

You should see a Liverpool 2-1 Arsenal mock match parse and produce feature snapshots.

---

## TASK 1 — Verify ESPN normalization on a real match

**Why:** I wrote the ESPN soccer normalizer (`normalize_espn` in `normalizer.py`) but couldn't test it against a real ESPN soccer payload from my sandbox. The play-type names and key paths might differ slightly from basketball.

**Do this:**
```bash
# Find a recent EPL match ID
python3 -m prophet.sports.cache_games list --sport soccer

# Or list manually:
curl 'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard' | python3 -m json.tool | head -50

# Pick an event_id, then run:
python3 -m prophet.sports.soccer.demo --source espn --event <ID>
```

**What to check:**
- Does it print events without crashing?
- Do you see shots, goals, subs, cards (not all `OTHER`)?
- Does the final score match reality?

**If something's off:**
- Open `normalizer.py`. Look at `ESPN_TYPE_MAP` — add any missing play types.
- ESPN's soccer summary may key events under `commentary` (already handled) or use different field names. Print `payload.keys()` to debug.

**Time budget:** 30 minutes.

---

## TASK 2 — Add momentum-based features

Right now the feature module tracks shots and dangerous events in rolling windows. The synthesis agent needs more **directional** signal.

**Add these features to `TeamFeatures` and compute them in `compute_features`:**

1. **xG (expected goals) accumulator** — if your data source provides shot xG values, sum them per team. ESPN doesn't expose xG; Understat does. For now, **approximate xG** by shot type:
   - On-target shot → 0.10 xG
   - Off-target shot → 0.05 xG
   - Blocked shot → 0.03 xG
   - Penalty → 0.78 xG
   - Free kick → 0.05 xG
   Add an `xg_total` and `xg_last_10` field.

2. **Pressure trend** — is pressure increasing or decreasing? Add `pressure_trend` ∈ {-1, 0, +1}:
   - Compare pressure in `[now-10, now-5]` vs `[now-5, now]`
   - +1 if the second window has more pressure events
   - -1 if it has fewer
   - 0 if equal

3. **Goal threat** — pressure_last_10 ≥ 3 AND shots_on_target_last_10 ≥ 1 → set a `imminent_goal_threat` boolean.

**Where:** edit `features.py`. The patterns to follow are already there in `compute_features`.

**Time budget:** 1 hour.

---

## TASK 3 — Substitution candidate detection

This is the *killer feature* for SidelineIQ — what makes the tactical agent useful.

**Add a `SubCandidate` model to `schema.py`:**

```python
class SubCandidate(BaseModel):
    side: Side                  # Which team
    reason: str                 # e.g. "yellow_card_in_first_half", "fatigue_proxy", "tactical"
    player_at_risk: str = ""    # The player most likely to come off
    urgency: int                # 1 (consider) ... 5 (do it now)
    note: str                   # 1-sentence explanation for the agent prompt
```

**Detect these patterns in `features.py` and attach a list of `SubCandidate` to `MatchSnapshot`:**

1. **"Yellow card on a midfielder in 1st half"** — player at heightened risk of 2nd yellow. Urgency 4. Note: "Risk of red — consider preemptive sub at HT or early 2nd half."

2. **"60-75' classic window"** — Urgency 2. Note: "Standard tactical refresh window."

3. **"Losing & late"** — Score state behind, minute ≥ 70. Urgency 3 for attacking sub. Note: "Need to chase the game — bring on an attacker."

4. **"Winning & late"** — Score state ahead, minute ≥ 80, opposition has 6+ shots last 10. Urgency 4 for defensive sub. Note: "Lock it down — defender / holding mid for fresh legs."

5. **"Striker drought"** — Team has 0 shots on target in last 20 min. Urgency 3. Note: "Forward line needs a new look."

**Implementation hint:** add a `detect_sub_candidates(events, snapshot) -> list[SubCandidate]` function. Don't bury the logic inside `compute_features` — keep detection separate so you can test patterns independently.

**Time budget:** 1.5 hours. This is the meatiest task.

---

## TASK 4 — Wire features into the synthesis prompt

Right now `MatchSnapshot.as_prompt_block()` produces markdown. The tactical agent (Qwen / GLM via Wafer) needs:

```python
# Skeleton — write this in prophet/sports/soccer/synthesis.py
SYNTHESIS_SYSTEM = """You are a soccer tactical co-analyst sitting beside the manager.
You see the live match state, recent events, and substitution candidates.

Your job: every 30 seconds, output ONE concise tactical insight (1-2 sentences).

Be specific. Reference actual players, minutes, and patterns.
Don't repeat yourself — if you said it last minute, find a new angle.
If nothing important is happening, say so honestly: 'Steady state, no action.'

Respond with ONLY JSON:
{
  "insight": "<1-2 sentence tactical observation>",
  "urgency": <int 1-5>,
  "category": "substitution|formation|defense|attack|set_piece|info"
}"""

def synthesize(snapshot: MatchSnapshot, history_summary: str = "", polymarket: str = "") -> dict:
    user = snapshot.as_prompt_block()
    if history_summary:
        user += f"\n\n## Historical context\n{history_summary}"
    if polymarket:
        user += f"\n\n## Market signal\n{polymarket}"
    return call_json(SYNTHESIS_SYSTEM, user, model=PRIMARY_MODEL, temperature=0.4)
```

**Then add an endpoint to `prophet/server.py`:**

```python
@app.post("/sports/soccer/synthesize")
async def synthesize_endpoint(match_id: str) -> dict:
    # Load the latest events for match_id, normalize, compute features,
    # call the synthesis agent, return the insight.
    ...
```

The frontend's "Insight card" on your diagram polls this every 30s.

**Time budget:** 1.5 hours.

---

## TASK 5 — Snowflake "History agent" (CAN BE FAKED FOR DEMO)

The History agent on your diagram queries Snowflake Cortex for "this team has lost their last 3 matches when their LB plays full 90."

**Honest advice:** Setting up Snowflake from scratch eats hours. For the demo, **fake the data layer** with a local SQLite or DuckDB file containing 1 season of one team's match summaries. Wrap it in a function called `query_history(team, situation)` that runs SQL.

In your writeup, say "Snowflake-loadable schema" and show the table structure. **Judges from Snowflake care about the integration pattern (Cortex Complete + tabular reasoning), not the data scale.** Show them a working query against a small table; they'll respect it.

If you have a teammate with Snowflake experience, real Snowflake is fine — just don't let it block the rest of the pipeline.

**Time budget:** 2 hours faked / 3-4 hours real.

---

## Don't do yet

- Real-time screen capture of match video → that's the Vision agent (Qwen2.5-VL), separate workstream
- Polymarket overlay → after synthesis is working
- ElevenLabs voicing → after insights are flowing
- Frontend → after synthesis endpoint returns useful JSON

**Build the spine first, then add limbs.** The order above gets you to a working tactical agent in ~5 hours of focused work.

---

## Quick reference

To run the existing data pipeline:
```bash
python3 -m prophet.sports.soccer.demo --mock                    # mock match, single snapshot
python3 -m prophet.sports.soccer.demo --mock --stream --stride 5  # 5-min snapshots
python3 -m prophet.sports.soccer.demo --source espn --event <ID>  # real EPL match
```

To explore the canonical types:
```bash
python3 -c "
from prophet.sports.soccer.schema import EventKind, MatchEvent, MatchSnapshot
import inspect
print([k.value for k in EventKind])
"
```

Ask me when you hit task 3 (the sub-candidate logic) or task 4 (the synthesis prompt) — those are where having a second pair of eyes helps most.
