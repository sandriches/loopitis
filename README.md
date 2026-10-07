# loopitis

a project that attempts to use a deep agent to turn unfinished loops into full arrangements.

an agent built on langchain's [deep agents](https://github.com/langchain-ai/deepagents).
give it a live set, it looks at the loops in session view and suggests an arrangement: which loops come in where, how long each section runs, and why. if you like it, it writes a new `.als` next to your original with everything laid out on the timeline and a locator at each section. the original file isn't touched.

## Running it

```bash
uv sync
cp .env.example .env   # add your ANTHROPIC_API_KEY
uv run langgraph dev
```

then open [agent chat UI](https://agentchat.vercel.app), point it at `http://localhost:2024` with graph id `loopitis`, and ask something like:

> Arrange the loops in ~/Music/My Track Project/My Track.als

it shows you the plan and stops before writing anything. You can approve it, edit it, or reject it with notes ("shorter intro, save the bells for the second drop") and it'll have another go.

it uses `claude-opus-5-5` by default. You can change the model in `.env`.

## How it works

an `.als` file is just gzipped XML, but a real one is around 14 MB, which is far too much to hand a model. so the parser boils it down to a 2 KB summary: tempo, key, and a line per loop with its length, range, chords and what it's probably doing (drums, bass, pad and so on). when the agent needs the actual notes, it hands that job to a subagent so the main conversation doesn't fill up with note lists.

the model makes the musical calls: section names, lengths, which loops play. everything else is plain code, including key detection, chord names, bar positions, and checking the plan makes sense (no two clips on one track, no empty clips) before anything gets written.

## Evals

this was tricky to test as there's no objective 'good arrangement'. however, a lot of finished live sets already contain the answer: you sketch loops in session view, drag them into an arrangement, and the original loops are still sitting there.

`loopitis-eval build` takes finished sets, strips out the arrangement and keeps the real one as a reference. The agent arranges the loops-only copy and gets scored on how close it gets to what the producer did: the order tracks come in, the energy curve, where sections change, which tracks get used, and the total length.

there's many ways to arrange a track, so treat the score as a rough guide. For context, here's how two simple rule-based arrangers do on 26 of my own tracks. The agent needs to beat these to be worth using.

| arranger | score |
|---|---|
| every loop playing for 96 bars | 0.50 |
| add a track every 8 bars, peak, strip back | 0.66 |

```bash
uv run loopitis-eval build ~/Music/Projects
uv run loopitis-eval baseline
uv run loopitis-eval run --limit 3   # add --langsmith to log it as an experiment
```

## Things to know

- it only sees structure. MIDI notes, clip positions and device names are readable; audio clips and third-party plugins are a black box.
- Only session-view loops get arranged. The new file starts from an empty arrangement, so anything that only lived in the old arrangement (recorded vocals, automation) won't be in it.
- I've only tested it with Live 12.

next up: showing the timeline as a proper visual in the chat instead of a markdown table.

## Tests

```bash
uv run pytest
```

everything runs offline, including a test that drives the full agent with a scripted fake model, approval step and all.
