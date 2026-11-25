# Coaching rules (single source of truth)

Every coach reads this file: the Sunday report task, the `run-analyst` subagent, and the
Claude Desktop chat (MCP tool `coaching_rules`). **To change how you're coached, edit this
file. Nowhere else.** Last updated: 2026-11-19.

## 1. Goals (priority order)

1. Get **faster at 5k**.
2. Then **10k**.
3. Then the **half marathon (21.1 km)**.

The physique goal (leaner and athletic) is **secondary**. Performance comes first.

Targets (set 2026-11-15; live in the **Goals** table, and `coach_snapshot.goals` shows the
gap to each):

| Goal | Target | Pace | By | Kind | VDOT needed (now 54.2) |
|---|---|---|---|---|---|
| 10k | **38:00** | 3:48/km | end Dec 2026 | checkpoint: no taper, a hard effort on a normal week | 55.2 |
| 5k | **17:40** | 3:32/km | end Mar 2027 | checkpoint: no taper | 57.6 |
| Half | **1:19:00** | 3:45/km | end May 2027 | **RACE: taper** for it (exact date TBC) | 59.3 |

- The half is the season's one real race, so the build peaks toward late May. The 10k and
  5k checkpoints are fitness tests along the way: slot them on a Tuesday or Saturday as
  that week's quality session, with no taper and no extra hard day.
- Judge progress by `vdot_gap` and `gap_seconds`, not by feel. The half needs the biggest
  jump: about +7 endurance VDOT, which mostly comes from volume and long runs. That's why
  protecting the long run and the 65–75 km/wk build matter most.
- If a target looks out of reach by its date, say so honestly and propose an interim target.

## 2. The week (Mon–Sat running, Sunday = the only rest day)

| Day | Session | Type |
|---|---|---|
| Mon | Easy + 6×100 m strides | easy |
| Tue | **Quality 1: VO2 / 5k intervals** (e.g. 5–6×1 km @ 5k pace, 10–12×400, 5×1000 @ 3k–5k). Rotate from `speed_workouts`. + light lower-body strength (later in the day) | intervals |
| Wed | Easy **medium-long** (10–14 km as volume builds) + strides. A key volume day | easy |
| Thu | **Quality 2: threshold / tempo**, the centrepiece for 10k→half. 5–6 km of cruise intervals at **threshold pace** (6×1 km / 60–75 s, 4×1.5 km, 2×2 km) or a continuous tempo. Comfortably hard, **not** intervals. + light upper/posterior strength | tempo |
| Fri | Easy run or easy bike + 4–6×100 m strides. No lifting | easy |
| Sat | **Quality 3: long run** with a progressing **HM-pace block**. Build toward 24–28 km, block toward 8–12 km. No separate tempo | long |
| Sun | Rest | rest |

Hard rules:
- **At most 3 hard days.** Never a 4th. No hard days back to back.
- **Protect the long run.** Don't swap Saturday for a 5k effort. If a time trial is wanted,
  put it on Tuesday in place of the intervals.
- **Thursday is threshold, not VO2.** If threshold reps drift to 5k pace or HR goes above ~175,
  that's too hard. Slow down.
  - Note (2026-11-19): 5x1 km @ 4:11, HR 169 (max 180), well controlled, but 7 s/km slower than
    the 4:04 target. Next Thursday, run the reps closer to **4:05**.
- A hard run on Sunday is off-plan. Flag it.
- Strength is secondary: 2 light sessions on Tue/Thu. Cut strength before cutting running.

## 3. Paces: always use `current_paces` (computed, never typed by hand)

`speed_workouts` / `coach_snapshot` return `current_paces`. These are training paces from the
best effort of 3 km or more in the last 6 weeks (Daniels VDOT). Endurance paces (threshold/HM/M)
are discounted while volume is under 60 km/wk. Easy pace is the athlete's own easy-run pace.

- Prescribe sessions at **current** paces. Goal paces show the direction, not the target for today.
- Easy = **HR under ~150**, whatever the pace.
- Say which pace source you used ("threshold 4:06 from the Nov 8 3 km TT").

## 4. Volume

- Build steadily toward **65–75 km/wk** (proven: 70–73 km/wk, summer 2024, injury-free).
- Levers in order: grow the long run → lengthen easy runs (Wed) → doubles (a 2nd 4–6 km easy
  run on 1–2 easy days) → strides.
- Ramp **≤ ~10 %/week**. Cut back **−25–30 % every 4th week**.
- Cycling is bonus aerobic and recovery. It does **not** replace running volume. It **does**
  count toward fatigue (TRIMP includes rides).
- Recovery re-entry: the first week after a real race = 1 quality session + long run.

## 5. Load and recovery signals

- **ACWR** (`training_load`): 0.8–1.3 safe building · >1.3 caution · >1.5 pull back ·
  <0.8 detraining. Keep < ~1.4 while building.
- **Readiness light** (`coach_snapshot.readiness`, also on the dashboard):
  - green = go as planned.
  - amber = do it controlled (cap reps/effort).
  - red = swap hard for easy or rest.
  - The light comes from last night's HRV vs the 7-night average (<90 % amber, <80 % red) and
    resting HR (+3 amber, +5 red).
- **Rising load + falling HRV or rising resting HR** = the clearest back-off signal.
- TRIMP depends on an estimated max HR. Trust the trend and the ratio over the absolute number.
- **Long-run alert**: no run of 16 km or more for 14+ days → rebuilding Saturday is the week's
  first priority.

## 6. Nutrition (performance-first, since 2026-11-10)

- Fuel the work. **No deliberate deficit.** Eat at maintenance on easy days and
  maintenance-plus on hard/long days.
- Protein **1.8–2.0 g/kg (~150–160 g) every day**. Carbs scale with volume (5–8 g/kg on
  hard/long days), around the session, plus carbs and protein within 30 min after.
- Flag **under-fuelling** (low intake or protein with suppressed HRV) as a performance and
  injury risk.
- If the athlete re-prioritises leaning out: a gentle deficit on easy days only.

## 7. Context the data can't show

`coaching/week_notes.txt` holds the athlete's own notes, tagged `[Week of START to END]`.
Use only the note for the week under review. Treat it as ground truth about intent. A session
the athlete changed on purpose (tired, ill, travel) is load management, not a miss.

## 8. How to talk to the athlete

- Lead with numbers, cite dates, be candid, keep it short.
- Every review moves the athlete one step closer to the goals:
  1. Was the week good or bad for the goals?
  2. Where they stand.
  3. The next concrete targets.
  4. How the plan changes.
