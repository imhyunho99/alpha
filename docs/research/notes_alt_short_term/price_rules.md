# Short-horizon price-based trading rules for altcoins: evidence and cost survival (Upbit retail lens)

Cost frame used below: an Upbit KRW-market round trip costs about 10 bps in fees (0.05% x 2), plus 5–30 bps of spread, plus slippage. That puts it at roughly 20–50 bps on liquid alts and more on thin ones. Upbit spot is long-only, so the short legs in academic long-short results are not available to this trader.

## Q1. Which short-horizon rules show significant returns, and how are they defined?

### Takeaway
The strongest and most-replicated cross-sectional finding is a horizon split. Over 1–4 weeks, past winners keep winning among large, liquid coins (weekly momentum). Small, illiquid coins reverse after a week, and daily reversal is very strong in the smallest, low-volume pairs. Composite trend signals that combine many price and volume indicators (CTREND) beat simple momentum. Time-series and technical rules (MA crossovers, breakouts, intraday momentum) show in-sample predictability, but the out-of-sample evidence is weak, especially for BTC.

### Cited Findings
**Cross-sectional momentum and reversal (weekly)**
- Liu, Tsyvinski & Wu, "Common Risk Factors in Cryptocurrency," *Journal of Finance* 77(2), 2022, pp. 1133–1177. The market, size, and momentum factors capture the cross-section, and momentum signals use 1–4 week lookbacks with quintile sorts (long Q5 minus Q1) rebalanced weekly — [EconPapers](https://econpapers.repec.org/RePEc:bla:jfinan:v:77:y:2022:i:2:p:1133-1177); [NBER WP 25882](https://www.nber.org/papers/w25882.pdf)
- A secondary summary puts the LTW momentum long-short at about 2.7%/week for 1-week momentum and about 4.1%/week for 3-week momentum. The snippet was fragmentary and not checked against the paper's tables — [Alpha Architect summary](https://alphaarchitect.com/2022/06/factors-investing-in-cryptocurrency)
- Dobrynskaya, "Cryptocurrency Momentum and Reversal," *Journal of Alternative Investments*, 2023. The sample is the 2,000 largest coins over 2014–2020, with formation and holding periods from 1 week to 2 years. Momentum is positive up to 2–4 weeks and reverses significantly beyond 1 month, mainly through past losers. Returns are large, significant, and not explained by standard crypto factors — [HSE publications](https://publications.hse.ru/en/articles/811744977)
- HSE press coverage of the same paper says momentum peaks with a 2-week sort, hold, and rebalance cycle, and reversal sets in from about 4–6 weeks — [HSE news](https://www.hse.ru/en/news/research/559533243.html)
- Fičura, "Impact of size and volume on cryptocurrency momentum and reversal," FFA Working Paper 5:2023.003. The weekly reversal reported in earlier work exists only in small, illiquid coins (t = −7.31). Large, liquid coins show weekly momentum (t = 2.33). "High momentum," measured as the distance of last week's close from the k-week high (here k = 1), works better than plain momentum: t = +4.93 for large/liquid coins and t = −9.03 for small/illiquid ones. No returns or costs are reported — [FFA WP](https://wp.ffu.vse.cz/artkey/wps-202301-0003_impact-of-size-and-volume-on-cryptocurrency-momentum-and-reversal.php)
- Günther, Fieberg & Poddig study more than 1,200 coins over 2014–2020 and find short-term reversal is the dominant characteristic in the cross-section — [IDEAS, Int. Rev. Fin. Analysis 78 (2021)](https://ideas.repec.org/a/eee/finana/v78y2021ics1057521921002349.html)

**Daily short-term reversal conditioned on volume**
- Bianchi, Babiak & Dickerson, "Trading volume and liquidity provision in cryptocurrency markets," Riksbank WP 413 (May 2022). Rule: each day, sort pairs on the previous day's return (t−1:t) into deciles, go long losers and short winners for t:t+1, and rebalance daily. A double-sort variant uses 3 return terciles x 3 terciles of de-trended volume (log volume relative to a 30-day trend, standardized). The sample covers USD pairs from 2017 to March 2022 — [Riksbank WP 413](https://www.riksbank.se/globalassets/media/rapporter/working-papers/2022/no.-413-trading-volume-and-liquidity-provision-in-cryptocurreny-markets.pdf)
- Gross results from that paper: the equal-weighted reversal earns 2.2%/day (daily Sharpe 0.37) and the value-weighted one 0.6%/day (daily Sharpe 0.06). Conditional on low volume, EW earns 1.22–1.26%/day versus 0.54% for high volume. Conditional on low volume, VW earns 0.6–0.65%/day, versus −0.18% for high volume (not significant) — [Riksbank WP 413](https://www.riksbank.se/globalassets/media/rapporter/working-papers/2022/no.-413-trading-volume-and-liquidity-provision-in-cryptocurreny-markets.pdf)

**Composite trend factor (weekly)**
- Fieberg, Liedtke, Poddig, Walker & Zaremba, "A Trend Factor for the Cross Section of Cryptocurrency Returns," *JFQA* (2024). CTREND combines technical indicators by machine learning: MAs, momentum oscillators, volume indicators, and Bollinger bands (20-day MA ±2σ). The sample is more than 3,000 coins from April 2015 to May 2022 (423 weeks), sorted into value-weighted quintiles rebalanced weekly. The long-short earns 3.87%/week (t = 5.19) with an annualized Sharpe of 1.94. The LTW 3-factor alpha is 2.62%/week (t = 4.22), and the momentum beta is 0.79, so CTREND is largely a better momentum signal — [JFQA / Cambridge](https://www.cambridge.org/core/services/aop-cambridge-core/content/view/S0022109024000747)
- CTREND also holds among large coins: a CTREND factor built on the 100 largest coins earns 2.45%/week gross and 1.90%/week net, both significant — [JFQA](https://www.cambridge.org/core/services/aop-cambridge-core/content/view/S0022109024000747)
- The same paper notes that "most cryptocurrency momentum strategies no longer produce significant profits at a 3- or even 2-week horizon," which means the signals decay fast — [JFQA](https://www.cambridge.org/core/services/aop-cambridge-core/content/view/S0022109024000747)

**Time-series momentum**
- Han, Kang & Ryu (Sungkyunkwan University), "Time-Series and Cross-Sectional Momentum in the Cryptocurrency Market: A Comprehensive Analysis under Realistic Assumptions." The evidence for TS momentum is strong and the evidence for CS momentum is weak once assumptions are realistic — [AUT ACFR PDF](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf)
- Shen, Urquhart & Wang, "Bitcoin intraday time-series momentum," *Financial Review* 57(2), 2022. Using volume to define the "trading day" for a 24/7 market, the first half-hour return predicts the last half-hour return in- and out-of-sample. The authors attribute it to liquidity provision and report market-timing gains, mainly in downturns. This is BTC only — [Birmingham portal](https://research.birmingham.ac.uk/en/publications/bitcoin-intraday-time-series-momentum/); [Reading accepted version](https://centaur.reading.ac.uk/100181)

**Technical rules (MA, breakout)**
- Detzel, Liu, Strauss, Zhou & Zhu, *Financial Management* 50(1), 2021. Price-to-moving-average ratios forecast daily BTC returns in- and out-of-sample. The working-paper version (July 2010–December 2017) reports gross annualized Sharpe ratios of 2.6 for a 5-day SMA down to 2.2 for a 100-day SMA, against 2.0 for buy-and-hold. These figures are gross and come from the early, illiquid BTC era — [IDEAS](https://ideas.repec.org/a/bla/finmgt/v50y2021i1p107-137.html); [CXO summary](https://www.cxoadvisory.com/currency-trading/timing-bitcoin-with-smas/)
- Hudson & Urquhart, "Technical trading and cryptocurrencies," *Annals of Operations Research* 297 (2021). About 15,000 rules across 5 classes on BTC and 3 other coins show significant predictability after data-snooping corrections (stationary bootstrap, Sullivan et al. 1999). However, there is no out-of-sample predictability for Bitcoin — [Reading](https://centaur.reading.ac.uk/85715); [Springer](https://link.springer.com/article/10.1007/s10479-019-03357-1)
- Svogun & Bazán-Palomino, *JIFMIM* 79 (2022), test 69 MA and breakout rules on daily and 1-minute data over 2016–2021. During bubbles the best rules are more likely to beat buy-and-hold for ETH, XRP, and LTC. Costs lower that likelihood for XRP and LTC — [IDEAS](https://ideas.repec.org/a/eee/intfin/v79y2022ics1042443122000816.html)

**Intraday seasonality**
- "The crypto world trades at tea time" (*Review of Quantitative Finance and Accounting*, 2024). Across centralized exchanges and pairs, returns are below average from 01:00 to 05:00 UTC (10:00–14:00 KST) and peak between 15:00 and 16:00 UTC (00:00–01:00 KST). The patterns are very similar across exchanges, time zones, and pairs. Per-hour magnitudes and cost tests were not retrieved — [Springer](https://link.springer.com/article/10.1007/s11156-024-01304-1)
- An SSRN study using hourly data on BTC, ETH, USDT, BNB, and 8 less-active coins concludes that hour-of-day patterns are not persistent daily phenomena. Its date and cost treatment were not verified — reported in search results alongside [Quantpedia, Bitcoin overnight seasonality](https://quantpedia.com/strategies/intraday-seasonality-in-bitcoin); Quantpedia lists transaction costs for the BTC seasonality strategy as "Not reported"

### Inferences
- For an Upbit KRW-alt universe, the cleanest testable long-only candidates are below.
  - (a) Weekly CS momentum or "near the 1-week high" among the top caps by volume: hold the top quintile for 1–2 weeks.
  - (b) A CTREND-like composite score, rebalanced weekly or every two weeks.
  - (c) Avoiding the small-cap mean-reversion trap: in illiquid alts, last week's winners tend to give the gains back.
- Daily reversal in illiquid pairs is mostly a liquidity-provision premium. Capturing it would mean posting limit orders (acting as a market maker), not crossing the spread with market orders.

### Gaps
- Exact LTW Table numbers (weekly momentum returns and t-stats) were not verified from the primary PDF.
- I could not retrieve full texts for Wen et al. (intraday return predictability), Grobys & Sapkota (2019, *Economics Letters*, reported no significant CS momentum), or any peer-reviewed study of volume or turnover spikes and pump-and-dump in altcoins. None of these are cited as findings here.
- I found no academic study of Upbit or KRW-market altcoin short-horizon rules. Search results were news items about Upbit volume surges and "fence pumping," which are not evidence.
- Day-of-week and weekend effects for altcoins after 2020 were not verified with a primary source.

## Q2. Gross returns and Sharpe, and what happens after costs (break-even levels)?

### Takeaway
Only a few papers model costs explicitly. CTREND survives with a high break-even cost (1.41% per trade), but it is a long-short, value-weighted academic portfolio rebalanced at closing prices. The daily reversal survives a 30/40 bps cost only on aggregated, equal-weighted data. It turns negative on Binance and loses significance when value-weighted. Intraday MA rules break even at about 0.36–0.4% per transaction and underperformed buy-and-hold out of sample.

### Cited Findings
- **CTREND:** turnover is 68% per week. With costs of 30/40, 40/50, or 50/60 bps (long/short), net returns are 2.90%/week (t = 3.89) to 2.35%/week (t = 3.16). The break-even cost is 1.41% per trade, and returns stay significant at 5% up to a 0.88% cost. Rebalancing every 2 weeks cuts the gross return to 2.34%/week, and returns lose significance beyond 4-week holds — [JFQA](https://www.cambridge.org/core/services/aop-cambridge-core/content/view/S0022109024000747)
- **Daily reversal (Bianchi et al.):** with a fixed 30 bps long and 40 bps short cost, the EW standard reversal still earns 1.68%/day (significant). All value-weighted strategies become insignificant net of costs, and VW alphas "drastically decrease and are no longer statistically significant." On single exchanges (Poloniex, HitBTC, GateIO, Bittrex, Binance, USDT pairs from 2018 to March 2022), gross returns hold up, but net performance is "significantly weaker, especially for the Binance exchange predicting the negative average returns." The authors also assume limit orders fill at the close with no slippage — [Riksbank WP 413](https://www.riksbank.se/globalassets/media/rapporter/working-papers/2022/no.-413-trading-volume-and-liquidity-provision-in-cryptocurreny-markets.pdf)
- **Han, Kang & Ryu:** estimating costs from actual fees, tick sizes, and slippage, and accounting for intraday price moves, "many momentum portfolios are liquidated." Many portfolios with statistically significant returns earn insignificant profits — [AUT ACFR PDF](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf)
- **Mroziewicz & Ślepaczuk (arXiv 2602.10785, 2026), intraday EMA crossover on BTC:** the rule goes long or short at 1–60 minute frequencies with walk-forward selection. In the training window, the annualized return falls from 111% at 0.05% per transaction to 29.5% at 0.3%, and turns negative (−3%) at 0.4%. Break-even is stated as about 0.36–0.4%. In the unseen period (November 2019 to August 2021, 0.1% cost), every WF variant underperformed buy-and-hold on BTC, ETH, and BNB. For example, BTC WF 7/28 returned 68%/yr against 93% for buy-and-hold — [arXiv](https://arxiv.org/html/2602.10785v1)
- **Detzel et al.:** reported performance is gross, and the authors flag large trading frictions in early BTC — [CXO summary](https://www.cxoadvisory.com/currency-trading/timing-bitcoin-with-smas/)
- **Momentum crash risk:** "Cryptocurrency momentum has (not) its moments" (*Financial Markets and Portfolio Management*, 2025) reports a momentum crash of about 255% in December 2020, which falls in the ex-post period used by LTW — [Springer](https://link.springer.com/article/10.1007/s11408-025-00474-9) (abstract and snippet only; full text was paywalled)

### Inferences
- The break-even numbers above are per-trade costs for academic portfolios, not per-trade edges for a retail account. Each signal still has to be converted to Upbit's long-only, market-order reality.
  - **CTREND-like weekly long-only:** 68% weekly turnover x about 40 bps round trip (Upbit fees plus spread on mid-caps) comes to roughly 27 bps/week of drag. That is small against a long-leg excess return on the order of 1–2%/week, if the effect still exists. The long leg alone is not reported in my sources, and part of the 3.87% spread comes from shorting losers.
  - **Daily reversal:** about 100% daily turnover on low-volume alts, where Upbit spreads are likely at the top of the 0.05–0.3% range. With 30–50+ bps round trip every day plus adverse selection, the results do not survive even on Binance. I judge it not implementable for a retail taker.
  - **Intraday MA on majors:** break-even is about 36–40 bps per transaction, against Upbit's roughly 10 bps fee plus about 5 bps spread on BTC/ETH. There is nominal headroom, but the out-of-sample alpha relative to buy-and-hold was negative even at 10 bps. For alts, wider spreads remove the headroom.
- Per-trade edge: weekly CS momentum or CTREND implies roughly 2–4%/week of gross long-short spread. Per position-turn that is hundreds of bps, so the edge comfortably clears 20–40 bps in the academic numbers. Daily and intraday rules imply edges of tens of bps per trade. That is about the same size as Upbit's round-trip cost, so they do not clear it robustly.

### Gaps
- None of the sources report long-only (top-quintile minus market) returns net of costs, which is the relevant number for Upbit.
- No study measured realistic spread or slippage on Upbit KRW alts. The 5–30 bps spread range is the task's own assumption, not a sourced figure.
- I found no published break-even analysis for the hour-of-day or weekend rules.

## Q3. Did the effects decay after 2020 or 2023 (out-of-sample, publication decay)?

### Takeaway
There is partial evidence of decay and very little true post-2022 out-of-sample testing. CTREND's raw returns fell in the second half of its sample (it still earned 3.26%/week in recent years, through May 2022). The daily reversal flattened after early 2021. BTC technical-rule predictability fails out of sample, and momentum suffered a large crash in December 2020. I found no clean 2023–2026 out-of-sample test of altcoin short-horizon rules.

### Cited Findings
- **CTREND subperiods** (April 2015–November 2018 vs. November 2018–May 2022): "an inevitable decline in profitability over time is visible," but returns in recent years still average 3.26%/week, and LTW alphas are nearly unchanged. Returns are higher in stable markets (5.46%) than in volatile ones (2.27%), and higher in bull markets (4.49%) than in bear markets (3.25%) — [JFQA](https://www.cambridge.org/core/services/aop-cambridge-core/content/view/S0022109024000747)
- **Daily reversal:** "the performance of the equal-weighted conditional reversal strategy flattens out after early 2021" — [Riksbank WP 413](https://www.riksbank.se/globalassets/media/rapporter/working-papers/2022/no.-413-trading-volume-and-liquidity-provision-in-cryptocurreny-markets.pdf)
- **Hudson & Urquhart:** no out-of-sample predictability for Bitcoin, although some remains for other coins — [Reading](https://centaur.reading.ac.uk/85715)
- **Later BTC/ETH technical-rule study (2023):** finds no profitable technical strategies for BTC/USDT and ETH/USDT in either the analysis or prediction samples after data-snooping adjustment — reported in search results alongside [IDEAS, "Are simple technical trading rules profitable in bitcoin markets?" Int. Rev. Econ. & Finance 93 (2024)](https://ideas.repec.org/a/eee/reveco/v93y2024ipbp858-874.html) (it is not confirmed that this listing is the same study)
- **Intraday EMA rules:** in the unseen period through August 2021, they underperformed buy-and-hold on BTC, ETH, and BNB — [arXiv 2602.10785](https://arxiv.org/html/2602.10785v1)
- **Crypto anomalies out of sample:** a Lasso-type factor study of 49 crypto anomalies finds out-of-sample behavior "noticeably different" from in-sample, with the size (market value) effect losing significance. A market + momentum + residual momentum model still works on post-LTW data — [ScienceDirect (2026)](https://www.sciencedirect.com/science/article/abs/pii/S0275531926000255)
- A 2025 arXiv paper says the LTW-style factors still explain crypto returns in the more recent period — [arXiv 2510.14435](https://arxiv.org/pdf/2510.14435)
- **General benchmark:** across equity anomalies, returns run about 26% lower out of sample and about 58% lower after publication (McLean & Pontiff 2016, as summarized) — [Micro Alphas](https://microalphas.com/factor-decay/); roughly half of the apparent decay may be publication bias — [Chen & Zimmermann, arXiv](https://arxiv.org/pdf/2209.13623)

### Inferences
- Plan for a gross edge 30–60% below the published numbers. Expect the decay to be concentrated in the most-traded and most-cited signals (simple 1-week momentum, BTC MA rules).
- Evidence that small-cap and low-volume effects drive most of the gross returns, together with Upbit's thin KRW alt books, suggests the effects that survive are the ones hardest to harvest.

### Gaps
- I found no primary study testing weekly CS momentum, reversal, or CTREND on 2023–2026 data.
- I found no study of whether the Korean-market premium or the episodic "fence pumping" seen in 2026 news changes short-horizon dynamics on Upbit.

## Q4. What turnover and per-trade edge do the rules imply? Can edge exceed 20–40 bps round trip?

### Takeaway
Only weekly-to-biweekly cross-sectional rules (momentum or near-high among liquid coins, or composite trend) show per-turn gross edges well above 40 bps in the literature. Daily reversal and intraday rules have edges comparable to or below Upbit's round-trip cost and fail on single-exchange or out-of-sample tests.

### Cited Findings
- **CTREND:** 68% weekly turnover, 3.87%/week gross long-short, and a break-even of 1.41% per unit of trading cost — [JFQA](https://www.cambridge.org/core/services/aop-cambridge-core/content/view/S0022109024000747)
- **Daily reversal:** daily rebalancing, gross 0.6%/day value-weighted, and insignificant net of a 30/40 bps cost when value-weighted. It is negative net on Binance — [Riksbank WP 413](https://www.riksbank.se/globalassets/media/rapporter/working-papers/2022/no.-413-trading-volume-and-liquidity-provision-in-cryptocurreny-markets.pdf)
- **Intraday EMA:** the break-even per transaction is about 0.36–0.4% — [arXiv 2602.10785](https://arxiv.org/html/2602.10785v1)

### Inferences
- **Concrete testable rule set for Upbit** (all long-only, KRW market, universe = top 30–50 by 30-day KRW traded value):
  1. **W-MOM:** every Monday 09:00 KST, rank on the 7-day return (or 14-day), buy the top quintile equal-weighted, and hold 7 days.
  2. **W-HIGH:** rank on close relative to the max of the past 7 days (Fičura "high momentum") and buy the top quintile, holding 7 days.
  3. **Composite:** z-score average of 1-, 2-, and 4-week returns, price/MA(20), and Bollinger position, rebalanced weekly or every 2 weeks to cut turnover.
  4. **Filter:** exclude the smallest and least liquid names, where weekly reversal dominates.
  5. **Hour-of-day:** if entries are discretionary, avoid entering between 01:00 and 05:00 UTC (10:00–14:00 KST) — weak evidence, test only.
  6. **Do not deploy** daily reversal, intraday MA, or intraday momentum rules as a market-order strategy.
- **Backtest requirements:** use next-bar open execution, model costs at 10 bps fee + the measured spread x 1 + an impact term, and include delisted KRW pairs (survivorship). Reserve 2023–2026 as a true holdout.

### Gaps
- Per-trade edge for a long-only top quintile is not reported in any source I retrieved, so it must be estimated from Upbit data.
- I found no evidence on short-horizon pump and volume-spike rules that meets the peer-review bar.
