# Desk roles

The desk has twelve roles in four stages. Run them in this order. Each role
sees only the inputs listed for it. The briefs mirror the prompts in
`tradinglab/agents/prompts.py`, so an agent-run desk and the API pipeline
follow the same procedure.

## The evidence rule (applies to every role)

Base the analysis only on the snapshot `context` block. Behave as if you know
nothing about this stock, its sector or the market after the analysis date.
Cite specific values from the snapshot. Do not invent numbers. If a metric is
missing, say it is missing.

## Stage 1: analyst team

Input for every analyst: the snapshot `context` block, nothing else. Each
writes a short structured report.

| Role | Runs when | Task | Report must contain |
|---|---|---|---|
| Market analyst | Always | Judge trend direction and strength from the moving-average gaps, MACD and ADX. Judge momentum and overbought or oversold conditions from RSI, the stochastic K/D/J values and CCI. Judge volatility and mean-reversion pressure from Bollinger %B, ATR and the distance from the 52-week high and low. Check whether volume and MFI confirm or contradict the price action. | Strongest bullish signals, strongest bearish signals, overall technical read for the next few trading days. |
| Fundamentals analyst | The snapshot has a Fundamentals block | Decide whether the company looks cheap or expensive for its growth and profitability. Flag balance-sheet and margin concerns. Say how the fundamentals support or undermine the recent price action. | Key positives, key negatives, overall fundamental read. |
| News analyst | The snapshot has headlines | Sort the headlines into likely positive, negative and neutral price drivers. Name the single most price-relevant story. Say whether each driver is company-specific, sector-wide or macro, and how long its effect should last. Use only the listed headlines. | The news backdrop and its likely net effect over the next few trading days. |
| Sentiment analyst | The snapshot has headlines | Infer investor mood from headline tone and from sentiment-sensitive technicals: momentum, volume behaviour and where the price sits in its recent range. Classify the regime as euphoric, constructive, neutral, fearful or capitulatory, and say whether it should persist or mean-revert. | The sentiment regime and what it implies for near-term positioning. |

## Stage 2: research debate

Input for Bull and Bear: the snapshot, all analyst reports, and the debate so
far. Run two rounds in the order Bull, Bear, Bull, Bear.

- **Bull researcher.** Make the strongest honest case for a long position.
  When the bear has spoken, rebut the bear's latest argument first. Concede
  weak points instead of denying obvious risks, then explain why the upside
  case still dominates. Keep each turn to a few sentences of rebuttal and
  two or three data-backed arguments.
- **Bear researcher.** Make the strongest honest case for a short position,
  or for avoiding the stock. Rebut the bull's latest argument first. Concede
  real strengths, then explain why the downside risks dominate. Same length
  limit.

Then the facilitator rules.

- **Facilitator.** Input: the snapshot, the analyst reports and the full
  debate. Weigh the quality of the evidence on each side, not the rhetoric.
  Reward arguments grounded in the snapshot and discount unsupported claims.
  Decide the balance of evidence for the next few trading days. Output:

  ```json
  {"verdict": "BULLISH|BEARISH|NEUTRAL", "rationale": "two or three sentences on the decisive evidence"}
  ```

## Stage 3: trader

Input: the snapshot, the analyst reports, the debate transcript and the
facilitator's verdict and rationale.

- **Trader.** Choose BUY to open or hold a long, SELL to open or hold a
  short, or HOLD to keep the existing stance. Choose a size from 0.0 to 1.0
  that reflects conviction. Size up only when the technical evidence, the
  analyst reports and the verdict agree. Size down or HOLD when they
  conflict. The trader may depart from the verdict, and must say why.
  Output:

  ```json
  {"action": "BUY|SELL|HOLD", "size": 0.0, "rationale": "two or three sentences"}
  ```

## Stage 4: risk review

Input for the three risk analysts: the snapshot, the trader's proposed action,
size and rationale, and the risk discussion so far. They do not see the
analyst reports or the research debate. Run one round in the order Risky,
Safe, Neutral. Each comment is a few sentences grounded in the snapshot.

- **Risky analyst.** Argue for capturing more upside. Show where the
  proposal is too timid for the strength of the evidence and what
  under-sizing costs. Acknowledge the main risks and say why they are
  compensated.
- **Safe analyst.** Argue for capital preservation. Name the scenarios in
  which the proposal loses money: volatility, crowded positioning, signals
  that disagree. Recommend a smaller size or HOLD whenever the downside is
  not clearly compensated.
- **Neutral analyst.** Weigh the two views impartially. Name the specific
  evidence that should settle the disagreement. Say whether the action and
  size look calibrated, and suggest a change only when the evidence clearly
  warrants one.

Then the fund manager decides.

- **Fund manager.** Input: the snapshot, the proposed trade and the risk
  discussion. Approve only when the evidence, the trader's rationale and the
  risk discussion are coherent. Otherwise reject the trade or cut its size.
  Output:

  ```json
  {"approved": true, "final_action": "BUY|SELL|HOLD", "final_size": 0.0, "rationale": "two or three sentences"}
  ```

## Normalization rules

Apply these exactly as the pipeline does, so results are comparable.

- An action other than BUY, SELL or HOLD becomes HOLD.
- A size outside 0.0 to 1.0 is clamped into that range.
- If the fund manager does not approve, the final decision is HOLD at size
  0, whatever `final_action` says.
- If a verdict cannot be determined, use NEUTRAL. If a trade decision cannot
  be determined, use HOLD at size 0.
- The final decision is the fund manager's `final_action` and `final_size`.
