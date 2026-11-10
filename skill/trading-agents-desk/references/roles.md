# Desk roles

The desk has twelve roles in four stages. Run them in this order. Each role
sees only the inputs listed for it. The briefs follow the prompts the API
pipeline uses, so an agent-run desk and the API pipeline apply the same
procedure. Those prompts are public:
https://github.com/Kantamaniprakash/trading-agents-lab/blob/main/src/tradinglab/agents/prompts.py

## The evidence rule (applies to every role)

- The evidence is the snapshot `context` block, including its "Data notes"
  section when it has one. A data note outranks a table: if a note says the
  last bar is a partial session, no role may treat that bar's close or volume
  as final.
- Use nothing you remember about this company, its sector or the market,
  whether from before or after the analysis date.
- Cite specific figures from the snapshot. Simple arithmetic on them is fine.
  Do not invent figures. If a figure is missing, say it is missing.
- General indicator conventions are allowed. Use the ones below so that
  every role reads a number the same way.

## Indicator conventions

| Reading | Convention |
|---|---|
| RSI(14) | Above 70 overbought, below 30 oversold. |
| Stochastic K and D | Above 80 overbought, below 20 oversold. KDJ J above 100 or below 0 is an extreme. |
| CCI(20) | Above +100 strong upside momentum or overbought, below -100 the reverse. |
| MFI(14) | Above 80 heavy buying pressure, below 20 heavy selling pressure. |
| Bollinger %B | 0.5 is the middle band. Above 1 is over the upper band, below 0 is under the lower band. |
| ADX(14) | Below 20 no clear trend, above 25 a trend. It measures strength, not direction. |
| MACD | Above its signal line is positive momentum, below is negative. A small gap is a weak reading. |
| ATR(14) | A typical daily range. The percent-of-close figure makes it comparable across stocks. |
| SMA gaps | Price above the 10, 50 and 200-day averages is an uptrend at that horizon, below is a downtrend. |

Volume has no average in the snapshot. Compare the rows of the price table
with each other, and leave out the last row when it is a partial session.

## Stage 1: analyst team

Input for every analyst: the snapshot `context` block, nothing else. Each
writes a report of about 150 words or fewer under the headings in the last
column.

| Role | Runs when | Task | Report headings |
|---|---|---|---|
| Market analyst | Always | Judge trend direction and strength from the moving-average gaps, MACD, ADX and the 1, 5, 21 and 63-day returns. Judge momentum and overbought or oversold conditions from RSI, the stochastic K/D/J values and CCI. Judge volatility and mean-reversion pressure from Bollinger %B, ATR, the 21-day volatility and the distance from the 52-week high and low. Check whether volume and MFI confirm or contradict the price action. | Bullish signals. Bearish signals. Technical read for the next few trading days. |
| Fundamentals analyst | The snapshot has a Fundamentals block | Decide whether the company looks cheap or expensive for its growth and profitability. Flag balance-sheet and margin concerns. Say how the fundamentals support or undermine the recent price action. Debt/equity is a percentage. The growth rates carry no stated period, so use them for direction and do not reconcile them with the quarterly lines. | Positives. Negatives. Fundamental read. |
| News analyst | The snapshot has headlines | Sort the headlines into likely positive, negative and neutral price drivers. Name the single most price-relevant story. Say whether each driver is company-specific, sector-wide or macro, and how long its effect should last. Use only the listed headlines, which are titles with no article text. Set aside any that are not about this company. When two headlines conflict, say so and do not settle it from memory. | News backdrop. Net effect over the next few trading days. |
| Sentiment analyst | The snapshot has headlines | Infer investor mood from headline tone and from sentiment-sensitive technicals: momentum, volume behaviour and where the price sits in its recent range. Classify the regime as euphoric, constructive, neutral, fearful or capitulatory, and say whether it should persist or mean-revert. | Sentiment regime. What it implies for near-term positioning. |

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

Input: the snapshot, the analyst reports, the debate transcript, the
facilitator's verdict and rationale, and the existing stance. The existing
stance is the user's stated position in this stock, or "no position" when
they gave none.

- **Trader.** Choose BUY to open or hold a long, SELL to open or hold a
  short, or HOLD to keep the existing stance. From no position, HOLD means
  stay out. Choose a size from 0.0 to 1.0 that reflects conviction. Size up
  only when the technical evidence, the analyst reports and the verdict
  agree. Size down or HOLD when they conflict. The trader may depart from
  the verdict, and the rationale must then say why. A size on a HOLD is
  ignored, so write 0.0. Output:

  ```json
  {"action": "BUY|SELL|HOLD", "size": 0.0, "rationale": "two or three sentences"}
  ```

## Stage 4: risk review

Input for the three risk analysts: the snapshot, the trader's proposed action,
size and rationale, and the risk discussion so far. They do not see the
analyst reports or the research debate, so their arguments must rest on
snapshot figures and the trader's own words. Run one round in the order
Risky, Safe, Neutral. Each comment is a few sentences.

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
  discussion. Approve the trade when the evidence, the trader's rationale
  and the risk discussion are coherent. Otherwise cut its size or reject it.
  To cut the size, approve with a smaller `final_size`. Setting `approved`
  to false is a rejection and leaves the book flat. The fund manager
  approves, cuts or rejects. It does not raise the size or reverse the
  direction. Output:

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
