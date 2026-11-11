# The research lab

This skill analyses current data only. Three things are not part of it:

- **The backtest harness.** Rule baselines, a walk-forward model and the
  agent desk, with lookahead prevented and trading costs always on.
- **The recorded API run.** The same twelve roles run through a model API with
  a fixed model per role, saved as a transcript and a record.
- **The memorisation probe.** A test of whether a model remembers a past
  window of prices.

They live in the open-source repository
https://github.com/Kantamaniprakash/trading-agents-lab
and are a separate install that the repository describes.

When the user asks whether the desk works, wants a backtest or wants a
recorded run:

1. Say that these are in the repository and give the link above.
2. Do not install or run anything from the repository as part of this skill.
3. Do not quote backtest figures from memory. The one test result this skill
   states is the test record that `render.py` prints on every report.

## One rule

Never run the desk on a past date to see how it would have done. A model may
remember what happened next, so the result would measure memory, not skill.
The lab's masked backtest and its probe exist for exactly this problem.
