# Structural / Event-Driven Short-Term Edges in Crypto for a Korean Retail Trader (Upbit KRW spot)

Constraints assumed throughout: Upbit KRW spot only, 0.05% fee per side (~0.1% round trip, before slippage), no shorting, no futures on Upbit, and capital controls that block cross-border arbitrage. The research covered roughly 15 search and fetch calls. Several primary PDFs (SSRN, ResearchGate) returned 403, so some findings rest on abstracts and search snippets. These are flagged where they occur.

## 1. Kimchi premium: does premium level or change predict later Upbit returns? Are there rules that need no cross-border transfer?

### Takeaway
The peer-reviewed literature explains why the Kimchi premium exists (capital controls amplifying Bitcoin network frictions) and shows it is persistent. It does not show that the premium level predicts BTC or KRW returns well enough to trade after costs. The only "predictive" rule found (zero-crossing from negative to positive, then hold 7 to 30 days) comes from a one-year, non-peer-reviewed industry piece with no event count and no cost model. The premium level itself has about -0.06 correlation with forward returns.

### Cited Findings
- From Jan 2016 to Feb 2018, BTC in Korea averaged 4.73% above US prices. Choi, Lehar and Stauffer attribute this to capital controls amplifying Bitcoin-network microstructure frictions. The same frictions explain EU-US gaps, but the effect is about 10x stronger for Korea. Capital controls are asymmetric (money gets into Korea more easily than out), so the premium stays positive on average. Premia for LTC, ETH and XRP are similar in size and highly correlated with BTC's, so switching coins does not get around the controls. The premium is *negatively* related to trading volume. (Pre-2021 data: flag as old.) — [Choi, Lehar & Stauffer, "Bitcoin Microstructure and the Kimchi Premium" (SNB seminar version)](https://snb.ch/dam/jcr:474400ba-255b-49a4-b2b8-5e3626719730/sem_2019_05_31_lehar.n.pdf)
- Makarov & Schoar find that cross-country arbitrage spreads widen when BTC appreciates. Spreads are not arbitraged away immediately, and they predict later *relative* returns across exchanges, i.e. convergence. This does not forecast the direction of BTC itself. (2017–2018 data.) — [Makarov & Schoar, "Trading and Arbitrage in Cryptocurrency Markets"](https://personal.lse.ac.uk/makarov1/index_files/CryptocurrencyMarkets.pdf)
- Eom (2021, *Finance Research Letters* v38) argued from unconditional regressions that the Kimchi premium is a speculative bubble, citing a positive relation with volume and volatility. Ok, Kim & Kim (2023, FRL v57) used time-varying coefficients and showed the relationship is not robust over time, so there is "no clear evidence" of a bubble. — [Ok et al. 2023 (RePEc)](https://ideas.repec.org:443/a/eee/finlet/v57y2023ics1544612323005792.html); [Eom 2021 (RePEc)](https://ideas.repec.org/a/eee/finlet/v38y2021ics1544612319301357.html)
- The premium peaked near 55% in Jan 2018 and then shrank (Lee & Oh 2022, FRL v50). — [RePEc](https://ideas.repec.org/a/eee/finlet/v50y2022ics1544612322004056.html)
- Seo, Koo & Yang (2024, *Economic Modelling* v135) estimate a non-zero long-run steady-state premium of 1.24% for BTC, consistent with a persistent violation of the law of one price. — [RePEc](https://ideas.repec.org/a/eee/ecmode/v135y2024ics0264999324000828.html)
- A 2024 arXiv paper ("Bitcoin and Shadow Exchange Rates") finds the BTC premium is strongest where capital controls are tight and exchange rates are managed. In this framing the premium is an FX and capital-control phenomenon, not an alpha signal. — [arXiv 2410.22443](https://arxiv.org/html/2410.22443v2)
- Industry, non-peer-reviewed (CryptoSlate, using CryptoQuant data, Jan 1 to Oct 13, 2025 only):
  - Kimchi-premium zero-crossings (negative to positive) were followed by an average +1.7% at 7 days (67% win rate) and +6.2% at 30 days (70% win).
  - Correlation of premium *level* with forward returns: about -0.06.
  - Coinbase-premium flips: roughly flat, about 55% win.
  - No event count, significance test, cost model or benchmark is reported. The author holds positions in the assets discussed.
  - [CryptoSlate, Oct 14 2025](https://cryptoslate.com/does-thes-kimchi-premium-still-front-run-btc/)

### Inferences
- With no cross-border transfer, the premium can only be a *timing signal* for Upbit spot. The academic evidence for a timing signal is essentially absent. Its documented predictive power concerns *convergence across venues*, which a Korean retail trader cannot capture under capital controls.
- The zero-crossing rule has a 7 to 30 day holding period, so the 0.1% round-trip fee is negligible. The real problem is statistical: one year, probably only a handful of crossings, and a 2025 sample in which BTC itself trended. A +1.7% 7-day average is within normal BTC drift and noise. A testable rule would be: enter Upbit BTC when the 1-day-smoothed premium crosses from below 0 to above 0, exit after N days, and compare against random-entry BTC holding across 2018–2026. This requires the historical KRW-USD premium series (computable from Upbit plus Binance/Coinbase candles and the USD/KRW rate).
- High premia (more than 10%) have historically coincided with local retail froth (2018, 2021). If anything, an *elevated* premium is a risk-off or contrarian flag for Upbit holders, because the premium itself can compress, a loss that KRW-only holders carry. This is an inference, not a tested result.

### Gaps
- I found no peer-reviewed 2021–2026 paper that tests premium level or change as a predictor of *Upbit KRW returns* net of costs. The Eom (2021) abstract and Kim et al. findings could not be retrieved in full.
- The "Kimchi premium on altcoins" (per-coin premia that diverge during deposit/withdrawal suspensions, the "가두리" pattern) has no systematic study in what I found.

## 2. Listing effects: how large, how fast, and do they drift or reverse? (Upbit/Bithumb KRW listings; Binance/Coinbase listings)

### Takeaway
Listing announcements produce large, very fast positive jumps: a median 24h peak of +34% for Upbit, with the median time to peak about 37 minutes, measured from the last pre-announcement trade on the most liquid venue (often an offshore perp). Holding afterwards is strongly negative: 2025 Upbit KRW listings bought at the day-1 close lost a median of 81% by March 2026. Academic studies from 2018–2021 find positive announcement-day abnormal returns (Coinbase > Binance) followed by negative drift from day 1. For an Upbit-only retail trader the jump is mostly gone before execution, and the reliable, structural part of the pattern is the post-listing *reversal*, which needs shorting to capture.

### Cited Findings
- **Vendor, non-peer-reviewed (CryptoListing.ws, 301 announcements Apr 9 to Nov 12, 2026):**
  - Reference price is the last trade before the announcement was detected; the "peak" is the best price within 24h on the token's most liquid pre-announcement market (often another exchange's perp).
  - 24h median peak moves:
    - Upbit spot listing: +34.2% (n=75)
    - Bithumb spot listing: +14.8% (n=50)
    - Binance spot listing: +49.7% (n=11, small sample)
    - Binance futures listing: +29.5%
  - Median time to peak:
    - Binance spot listing: 10 minutes
    - Upbit spot listing: 37 minutes
    - Bithumb spot listing: 2.2 hours
  - Only tokens that already traded elsewhere are measurable. "Peak" is a best-trade, look-ahead measure, not an achievable return, and no retention or reversal statistics are given.
  - [CryptoListing.ws listing price reaction](https://cryptolisting.ws/listing-price-reaction/)
- **Industry research, non-peer-reviewed (Four Pillars, 2025 Korean CEX listings post-mortem):**
  - Method: every token that got a new KRW pair in 2025, bought with $100 at the **day-1 close** (deliberately skipping the inflated open), held to Mar 11, 2026. Delisted tokens (ELX, STRIKE, AI16Z) count as -100%.
  - Upbit, 59 tokens: portfolio -69.5%, median -80.9%. Only 2 of 59 finished positive.
  - Bithumb, 144 tokens: portfolio -69.1%, median -82.1%.
  - Binance's 92 new listings: -71.7%.
  - Bithumb H1 2025 listings: -77.3%; H2 2025 listings: -59.4%.
  - No BTC benchmark and no USD/KRW adjustment. Part of the loss is the 2025–26 alt bear market (the BTC widget on the CryptoSlate page showed about $61.6k in Nov 2026 versus $110–125k in Oct 2025), so not all of it is listing-specific.
  - [Four Pillars](https://research.4pillars.io/en/research/2025-korean-cex-listings-post-mortem)
- Erasmus MSc thesis (Laucht), 390 cross-listing events on 11 CEXs, Jan 2020 to Dec 2021: significant positive abnormal returns *leading up to* the listing (leakage or anticipation), about +2.0 to 2.4% abnormal return on listing day, and **negative abnormal returns from day 1 after listing**. This is a student thesis, not peer-reviewed. — [Laucht thesis](https://thesis.eur.nl/pub/65625/Laucht_603384_Thesis.pdf)
- Li, Luo, Wang & Wei (AFT 2025): coins listed on both Coinbase and Binance show significantly positive short-run listing returns, and the Coinbase return is significantly higher. They attribute this to certification; Binance tends to list its venture arm's investments, which have weaker fundamentals. A conference slide deck by J. Li gives average first-day announcement returns of 32.8% (Coinbase) and 22.7% (Binance), from a search snippet; I did not verify the sample period. — [Dagstuhl AFT 2025 abstract](https://drops.dagstuhl.de/storage/00lipics/lipics-vol354-aft2025/html/LIPIcs.AFT.2025.11/LIPIcs.AFT.2025.11.html); [Li slides (Loyola)](https://loyola.edu/_media/sellinger-business/documents/academics/departments/finance/wealth-management-conference/speakers/J_Li.pdf)
- Messari (2021, industry): Coinbase new listings averaged +91% over 5 days, with a range of -32% to +645% across 28 tokens. Controlling for outliers the average was about +29%. This sits at the peak of the 2021 bull market and is heavily skewed. — [CoinDesk on Messari](https://www.coindesk.com/markets/2021/04/07/coinbase-effect-means-average-91-token-price-gain-in-5-days-messari-says/)
- An older academic study (ResearchGate, "Market Reaction to Exchange Listings of Cryptocurrencies", c. 2019; I believe the author is L. Ante but could not verify because of a 403) reportedly found Upbit was the only exchange with a *significantly negative* announcement CAAR: -2.9% (p<.05) across 15 events, only 20% positive. Pre-2021, tiny sample, and contradicted by the 2025–26 jump data. — [ResearchGate](https://www.researchgate.net/publication/335690724_Market_Reaction_to_Exchange_Listings_of_Cryptocurrencies)
- Anecdotes: CAP rose about 34.5% within one minute of an Upbit listing notice — [BigGo Finance](https://finance.biggo.com/news/e5663b55-0efb-49b8-9fd7-1cb7db04d3cd). SIGN went from $0.08 to $0.129 after its Upbit listing (Apr 2025) — [CoinDesk](https://www.coindesk.com/business/2025/04/29/sign-rises-60-on-upbit-listing-despite-slow-start-on-binance).
- Korean press (2021) described a recurring "listing pump" with extreme volatility right after KRW listing, which persisted even after the Special Act reporting regime. — [Asia Economy 2021-10](https://www.asiae.co.kr/en/article/2021102810085962838)

### Inferences
- **Latency:** The jump happens on offshore venues (Binance spot and perps) within seconds to minutes of the Upbit notice. A new Upbit KRW market usually opens some time after the announcement, and its first prints are typically above the offshore post-jump price. An Upbit-only trader therefore has to buy the *already-listed-elsewhere* token on Upbit, which is impossible for a brand-new KRW listing, or buy at the inflated open. The tradable version of "buy on announcement" needs offshore spot or perp accounts plus sub-minute notice scraping. The Upbit-spot constraint kills it.
- **Upbit BTC-market to KRW-market upgrades** (a token already on Upbit's BTC or USDT market getting a KRW pair) are the one case where an Upbit-only trader could in principle react in the same venue. I found no measured stats for this subcase; it should be tested on Upbit's own notice history and candles.
- **Reversal is the robust structural pattern,** and the only way to exploit it is *not buying*. Without shorting, the practical rule is a negative filter: never hold newly listed KRW tokens beyond the listing day, and treat "new listing within N days" as an exclusion in any alt-momentum universe. This is a cost-free, high-confidence improvement.
- Survivorship and look-ahead warnings: "peak within 24h" statistics overstate capturable returns. The Four Pillars buy-and-hold result is confounded by the 2025–26 market drawdown; it needs a BTC- or alt-index-relative version.

### Gaps
- No peer-reviewed 2021–2026 event study of Upbit or Bithumb KRW listings with abnormal returns at minute, hour and day horizons was found. KCI and DBpia were not reachable through this search.
- No data on the gap between announcement time and Upbit KRW trading open, or on the open-price premium versus offshore.

## 3. Funding rates and basis: do extreme funding rates predict spot returns at short horizons?

### Takeaway
The evidence is mixed and weak. Extreme-funding events have fallen about 90% since 2016. One working paper finds direction-dependent effects: reversal after extreme funding on up days, continuation on down days, each about ±1.8% over 24h. Another finds extreme *negative* funding is followed by about +0.5% over 24h on BTC. A longer-horizon industry claim (negative 30-day average funding preceding strong 90-day returns) rests on few episodes. The carry trade (long spot, short perp) has collapsed and needs a short perp anyway. For Upbit spot, funding can be at most a coarse, free regime filter.

### Cited Findings
- He, Manela, Ross & von Wachter, "Fundamentals of Perpetual Futures" (2022–2024 versions): perp-spot deviations comove across cryptos and have diminished over time as markets mature. — [arXiv 2212.06888](https://arxiv.org/html/2212.06888v7)
- Crypto carry (long spot, short perp), cited in a 2025 survey drawing on Schmeling, Schrimpf & Todorov (2023): annualized Sharpe 6.45 over 2020–2025, 4.06 from 2024, and **negative in 2025**. This is strong evidence of decay. — [arXiv 2510.14435](https://arxiv.org/pdf/2510.14435)
- BitMEX study (company research, non-peer-reviewed), May 2016 to May 2025: the occurrence of extreme BTC perp funding rates fell about 90%. — [Investing.com / Chainwire](https://www.investing.com/news/cryptocurrency-news/bitmex-study-reveals-90-drop-in-extreme-bitcoin-perpetual-futures-funding-rates-since-2016-signalling-market-maturation-4116930)
- Shek, "Conditional Arbitrage Capacity: Evidence from Crypto Perpetual Funding Rates" (SSRN working paper; abstract via search snippet, the page returned 403): 274 extreme-funding events across BTC, ETH and SOL, 2021–2026. Events on up days revert by +1.88% over the next 24h (as reported in the snippet; the sign convention is unclear), and events on down days continue by -1.74%. The effect is direction-conditional, not a simple contrarian rule. — [SSRN 7520320](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7520320)
- Memon (AIJMR 2026, low-tier journal): Binance BTCUSDT 15-minute data, extreme funding defined by percentile within a rolling 180-day window. Extreme *negative* funding is followed by a mean 24h BTC return of +0.506%, significant by t, Wilcoxon and directional tests. The relationship is nonlinear, asymmetric and horizon-dependent. — [AIJMR PDF](https://www.aijmr.com/papers/2026/4/1493.pdf)
- K33 Research (industry, via The Block, Sep 2024): since 2018, after the 30-day average funding rate turned negative, the average 90-day BTC return was 79% (median 55%). Few independent episodes, and the method is not disclosed. — [The Block](https://www.theblock.co/post/315831/perp-signal-bitcoin-bottom-bullish-year-end-k33)
- A personal research blog reports that extreme funding "barely predicts" price. Unvetted. — [bryanvine.github.io](https://bryanvine.github.io/alpha-research/paper2.html)

### Inferences
- The one funding signal an Upbit spot trader can act on is *extreme negative funding* (crowded shorts), used as a long entry for BTC, ETH or SOL on Upbit. Reported edges are +0.5% to +1.9% over 24h, against about 0.1% round-trip fees plus slippage plus the Kimchi-premium drift over the hold. Net edge would be small and fragile, and the event count is low (274 events over about 5 years across 3 coins, i.e. about 18 per coin per year).
- Funding data is free (Binance/Bybit public APIs), so this is cheap to test. Proper walk-forward is required because the "extreme" percentile threshold must be computed only from past data; both papers appear to use rolling windows, but this is unverified.
- Positive-funding extremes (crowded longs) imply avoiding or reducing exposure, which is again a filter rather than a trade without shorts.

### Gaps
- No peer-reviewed 2023–2026 paper directly tests funding as a predictor for *altcoin* spot returns at 1 to 7 day horizons net of costs.
- Basis (dated futures, CME) as a short-horizon spot predictor: no specific 2021–2026 results were found in this pass.

## 4. Pump-and-dump and Upbit "investment warning" (투자유의) designations: is there a predictable reversal? Do retail followers lose?

### Takeaway
Academic work on Telegram and Discord pump-and-dumps (mostly 2018–2021 data) consistently finds that organizers and early insiders profit while late participants lose. I found no systematic quantitative study of Upbit 투자유의 designation returns. The evidence is anecdotal and goes both ways: immediate drops of 2% to 10% after designation, and occasional speculative squeezes on designated or delisting-notice coins, e.g. Steem Dollars (SBD) up about 521% after designation in Dec 2024 to Jan 2025. Korean regulators report that post-2024-07 enforcement targets exactly these ultra-short manipulation patterns.

### Cited Findings
- A study of 2018 activity counted about 2,469 Telegram pumps (plus 952 on Discord) over 6 months. Bloomberg reported 4,818 pump attempts Jan to Jul 2018. Organizers and insiders profit and latecomers suffer large losses. (Old data.) — [Xu & Livshits, arXiv 1811.10109](https://arxiv.org/pdf/1811.10109); [Bloomberg 2018](https://www.bloomberg.com/news/articles/2018-12-19/crypto-market-rife-with-pump-and-dump-schemes-study-shows)
- Clough & Edwards (Bristol, 2023): long-term impact on 765 pumped coins; participants often lose money. I did not retrieve the full results. — [arXiv 2309.06608](https://arxiv.org/pdf/2309.06608.pdf)
- A real-time ML detection paper (2024) exists for pump-and-dump detection. — [arXiv 2412.18848](https://arxiv.org/html/2412.18848v2)
- Upbit 유의종목 procedure: Upbit reviews a designated coin for about one week (extendable). If the reason for designation is not resolved, trading is terminated. — [디지털데일리 2021](https://www.ddaily.co.kr/page/view/2021061415193457988)
- In 2021, prices pumped mainly in Upbit's designated (유의) coins, and coins whose KRW market closure was announced fell more than 40% immediately. — [디지털데일리 2021](https://www.ddaily.co.kr/page/view/2021061415193457988)
- In Jan 2025, SBD went from 2,800 KRW at designation (Dec 30) to 17,400 KRW (Jan 9), about +521%. A Despread Research analyst attributed this to buying flowing into coins with similar status after designation and delisting notices. Single case, extreme selection. — [뉴시스 2025-01-10](https://www.newsis.com/view/NISX20250110_0003028508)
- July 2025: after Upbit warning notices, TT fell 6.62%, JASMY 5.25% and STORJ 1.98% within minutes. These later moved to delisting. — [Coingape](https://coingape.com/upbit-announces-storj-jasmy-and-tt-delisting-after-warning-review/); [BeInCrypto](https://beincrypto.com/upbit-delisting-3-altcoins-august/)
- CryptoListing.ws (vendor, 2026): Upbit spot delisting notices show a median 24h low of -12.7% (n=15, small) with a median time to low of 12.9h. Binance Monitoring Tag: -15.2% median, low reached in 1.5h. Bithumb delisting: -21.3%. — [CryptoListing.ws](https://cryptolisting.ws/listing-price-reaction/)
- Much syndicated coverage of Upbit warnings (BitcoinWorld via MEXC) appears templated and repeats dates across unrelated tokens. Treat figures from it as unreliable.
- FSC (as of Jul 2026, about 2 years after the 2024-07-19 가상자산이용자보호법 took effect):
  - About 40 unfair-trading cases investigated, 25 suspects identified, about 30 cases referred to prosecutors, mostly market manipulation. Average illicit gain per suspect was 1.4bn KRW, and an average of 8 coins were manipulated per case.
  - Typical ultra-short tactics: **"경주마"** (concentrated orders at the time the price-change-rate display resets, to lure buyers) and **"가두리"** (pumping on an exchange while deposits and withdrawals are suspended, so arbitrage cannot cap the price).
  - Other methods: API-key rental, issuer-linked cases, and offshore-linked whale manipulation.
  - Real-time and AI-based surveillance plus exchange reporting are now in place.
  - [한국경제 2026-07](https://www.hankyung.com/article/202607192400i); [YTN 2026-07](https://www.ytn.co.kr/_ln/0102_202607191407477162)

### Inferences
- **No positive-expectancy long rule** comes out of the warning-designation evidence for a no-short trader. Designated coins either drop immediately (too fast to sell ahead of) or, rarely, squeeze as manipulation targets. Chasing the squeeze is exactly the 경주마/가두리 victim profile the FSC describes.
- Practical rules, mostly defensive:
  1. Exclude 유의 and delisting-notice coins and any coin with a deposit/withdrawal suspension (가두리 risk) from the trading universe.
  2. Treat abnormal volume spikes in low-cap KRW-only coins as manipulation risk, not momentum.
  3. Exit holdings immediately on designation, since the median delisting reaction plays out over hours (12.9h median to low on Upbit), so an early exit avoids part of it.
- An abnormal-volume reversal rule (e.g. after a +X% day on volume more than k times normal, the next-N-day return is negative) is plausible from the pump-and-dump literature and testable on Upbit candles. Profiting from it requires shorting, so on Upbit it again becomes a filter.

### Gaps
- No academic or quantitative study of Upbit 투자유의 designation returns across all designations was found. KCI and DBpia need direct access.
- No quantified retail P&L for Korean participants in listing or warning pumps.

## 5. Decay after 2023 and the effect of Korean regulation (가상자산이용자보호법, 2024-07-19)

### Takeaway
There is clear decay in derivatives-based edges: carry Sharpe turned negative in 2025 and extreme-funding frequency is down 90%. The Kimchi premium has compressed from about 55% (2018) to low single digits, with a 1.24% estimated steady state. Listing pumps have *not* disappeared (Upbit median +34% 24h peak in 2026), but post-listing returns are deeply negative. The 2024 law has brought enforcement against ultra-short manipulation, but I found no before/after quantification of listing-pump or warning-coin behaviour.

### Cited Findings
- Carry Sharpe: 6.45 (2020–25), 4.06 (2024 onward), negative in 2025. — [arXiv 2510.14435](https://arxiv.org/pdf/2510.14435)
- Extreme funding occurrence down about 90% since 2016. — [BitMEX via Investing.com](https://www.investing.com/news/cryptocurrency-news/bitmex-study-reveals-90-drop-in-extreme-bitcoin-perpetual-futures-funding-rates-since-2016-signalling-market-maturation-4116930)
- Perp-spot deviations diminish over time. — [He et al.](https://arxiv.org/html/2212.06888v7)
- Kimchi premium: peaked near 55% in Jan 2018 and has since declined; long-run level estimated at 1.24%. Around 4% in Oct 2025, described as 1.35 SD above its 2025 mean. — [Lee & Oh 2022](https://ideas.repec.org/a/eee/finlet/v50y2022ics1544612322004056.html); [Seo et al. 2024](https://ideas.repec.org/a/eee/ecmode/v135y2024ics0264999324000828.html); [CryptoSlate](https://cryptoslate.com/does-thes-kimchi-premium-still-front-run-btc/)
- Listing pumps persist in 2025–26 and the reversal is severe. — [CryptoListing.ws](https://cryptolisting.ws/listing-price-reaction/); [Four Pillars](https://research.4pillars.io/en/research/2025-korean-cex-listings-post-mortem)
- 2024 law enforcement statistics and manipulation typologies: see section 4. — [한국경제](https://www.hankyung.com/article/202607192400i)

### Inferences
- Overall, for Upbit-spot retail traders:
  - **Kimchi premium:** no proven timing edge. At most, use it as a froth or risk flag.
  - **Listing announcements:** the move is captured offshore within minutes, so it is not capturable on Upbit spot. Post-listing reversal is the robust pattern, usable only as an avoidance filter.
  - **Funding extremes:** weak, decaying, small edge (about 0.5% to 1.9% per 24h event) on few events. Worth a cheap walk-forward test as a BTC/ETH/SOL entry trigger or filter, using free offshore funding data.
  - **Warnings, pumps and abnormal volume:** defensive filters only.
- The cheapest, highest-confidence takeaways are exclusion rules: new listings under N days old, 유의 or delisting-notice coins, coins with deposit/withdrawal suspensions, and abnormal-volume microcaps. They reduce losses rather than generate alpha.
- Look-ahead and cost cautions apply to every reported number: "peak within 24h" stats, single-year zero-crossing stats, and gross carry Sharpes all overstate what is achievable.

### Gaps
- No before/after study of listing-pump magnitude or 유의-coin behaviour around 2024-07-19.
- No Bank of Korea or FSC report quantifying the Kimchi premium's behaviour after 2024 was retrieved.
- Korean academic journals (한국증권학회지, 금융연구, KCI) could not be searched effectively here; a direct KCI/DBpia query is recommended for Korean-language listing and warning event studies.
