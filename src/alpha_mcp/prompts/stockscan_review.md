+++
name = "stockscan_review"
title = "StockScan monthly review"
description = "Review one DNA Rynków StockScan edition (PDF): its Top Picks and worst-rated names for GPW or foreign stocks, each with industry, a one-line profile, a rating-based stance and Yahoo Finance basics."

[[arguments]]
name = "month"
description = "Edition month as YYYY-MM (publication month, e.g. 2026-05). A Polish month name plus year ('maj 2026') also works."
required = true

[[arguments]]
name = "market"
description = "pl = GPW companies (Spółki GPW). us = the foreign-companies section (Spółki zagraniczne; mostly NASDAQ/NYSE, with a few European and Asian listings)."
required = true

[[arguments]]
name = "count"
description = "How many worst-rated names to list, and how many extra +2 names to add after the official Top Picks."
default = "5"

[[arguments]]
name = "reports_dir"
description = "Folder that holds the StockScan PDFs."
default = "/home/surtr/projects/alpha/stock-scan"
+++
# TASK

Review the StockScan edition of **{{month}}** for the **{{market}}** market: list the top picks and the worst-rated companies, and enrich every name with data from Global.db and Yahoo Finance. Today is {{today}}. StockScan is a monthly PDF by DNA Rynków (dnarynkow.pl); it is in Polish, the summary you write is in English.

## 1. Find the edition

Polish month names, as they appear in file names and on the cover (diacritics are often dropped in file names): styczeń/styczen, luty, marzec, kwiecień/kwiecien, maj, czerwiec, lipiec, sierpień/sierpien, wrzesień/wrzesien, październik/pazdziernik, listopad, grudzień/grudzien.

List `{{reports_dir}}` and take the PDF whose name contains the month name and the year (match case-insensitively, ignore diacritics; names look like `StockScan-Maj-2026.pdf` or `stockscan-wrzesien-2026-294942.pdf`). Confirm with the cover: `pdftotext -f 1 -l 1 "<pdf>" -` prints `Edycja <N>` and `Publikacja: <day> <month> <year>`. If no file matches, list what you found and stop.

Normalise `{{market}}`: `pl`, `gpw`, `poland` mean the GPW section; `us`, `foreign`, `zagraniczne` mean the foreign section.

## 2. Layout of an edition

About 200–225 pages, one page per company: roughly 100 GPW companies, then roughly 90–100 foreign ones. Locate sections by their text, not by fixed page numbers:

- Page 3: table of contents.
- `Podsumowanie Top Picks <from> – <to>` (pages 5–7): last period's picks with their returns, and the Top Picks portfolio result versus the benchmark (`Wynik StockScan = …`, `Wynik benchmark = …`).
- `Top Picks <from> – <to>` (page 8): the new picks in two columns, `Spółki GPW` and `Spółki zagraniczne`, plus a `MID TERM TOP PICKS` list with notes such as `(obecny od 2M)` (on the list for 2 months) or `(nowy podmiot)` (new).
- `Kilka słów o rynku ogółem`: market overview, then `Przegląd spółek GPW` (index of GPW companies), the GPW company pages, `Przegląd spółek zagranicznych` (index), the foreign company pages.
- `Legenda do podsumowania ratingów`, then two pages `Podsumowanie aktualnych ratingów oraz zmian w ratingach`: the first is GPW, the second is foreign. Each row is: change month-on-month (coloured cell), company name, current rating. Rows are sorted from the largest upgrade to the largest downgrade.
- `Model Reverse DCF` and `O nas` close the edition.

The rating is a newsflow-sentiment rating from -2 (`fatalny newsflow`) to +2 (`świetny newsflow`). The publisher states it is not a buy or sell recommendation; the Top Picks are the +2 (or strongly upgraded) names where the analysts also see a catalyst.

A company page has: the header `NAME (EXCHANGE:TICKER)`, e.g. `CD PROJEKT (GPW:CDR)` or `Apple (NASDAQ:AAPL)`; a profile paragraph (what the company does); the commentary (why the rating is what it is); and the rating box. The rating box has two text forms:

- editions up to May 2026: `RATING`, then `Aktualny Poprzedni`, then the two numbers `<current> <previous>` on the next line (in `-raw` mode these three lines are printed together, before the profile);
- editions from June 2026: `Aktualny: <current>` and `Poprzedni: <previous>`.

The sector is `Sektor: <name>` (May 2026), a sector-group label near the header such as `Konsument`, `Finanse`, `Platformy technologiczne i internetowe` (from September 2026), or absent (June 2026).

Text-extraction traps: from June 2026 every page carries a price chart whose axis labels (`Kurs`, `Rating (prawa oś)`) and stray numbers between -2 and 2 land in the text, so take the rating only from the `Aktualny`/`Poprzedni` lines; some copies carry a diagonal watermark that shows up as scattered letters, ignore it; the report's tickers occasionally contain typos (`SNG` for Sygnity, whose ticker is `SGN`); the summary table is a multi-column grid that text extraction scrambles, so read it as an image.

## 3. Extract

```
pdftotext -layout "<pdf>" "<tmp>/stockscan.txt"     # page N = N-th form-feed-separated block
pdftotext -raw -f N -l N "<pdf>" -                  # one company page in reading order
pdftoppm -r 80 -png -f N -l N "<pdf>" "<tmp>/page"  # render a page, then view the PNG
```

1. **Top Picks page.** Take the column for the market (`Spółki GPW` or `Spółki zagraniczne`) and the `MID TERM TOP PICKS` names that belong to it (a mid-term name belongs to the market whose section holds its company page).
2. **Summary table.** Render the summary page for the market (first page GPW, second page foreign; confirm by the names on it) and read every row: change, name, current rating. This is the full universe for ranking; it is more reliable than the scrambled text.
3. **Company pages.** For every name you will report, find its page (search the header `(EXCHANGE:TICKER)` or the upper-cased name), confirm the current and previous rating there, take the profile sentence and the one or two sentences that explain the rating, and note the page number. If the text is ambiguous, render the page and read it.

## 4. Select

- **Top picks**: every official Top Pick for the market, then the mid-term picks (label them `mid-term`), then up to {{count}} more names rated +2 that are not already listed, upgrades first (largest change month-on-month), then alphabetical.
- **Worst rated**: the {{count}} lowest names: sort by current rating ascending, then by change ascending (downgrades first), then alphabetical.
- **Notable moves**: every rating change of two notches or more, and every move into +2 or -2, that is not already in the two lists.

## 5. Enrich

**Yahoo symbol.** Map the report's exchange prefix:

| Report | Yahoo symbol |
|---|---|
| GPW | `TICKER.WA` |
| NASDAQ, NYSE, NYSEAMERICAN | `TICKER` |
| EPA (Paris) | `TICKER.PA` |
| AMS, AS (Amsterdam) | `TICKER.AS` |
| ETR, XETRA (Frankfurt) | `TICKER.DE` |
| LON, LSE (London) | `TICKER.L` |
| HEL (Helsinki) | `TICKER.HE` |
| KRX (Korea) | `CODE.KS` |
| TSE, TOPIX (Tokyo) | `CODE.T` |
| CVE (TSX Venture) / TSX | `TICKER.V` / `TICKER.TO` |

**Global.db.** Call `get_row("Tickers", "<yahoo>", key_column="Yahoo")` for each symbol and use `Name`, `Sector`, `FMP_Industry`, `FMP_Sector`, `Exchange`, `Country`, `ISIN` and `FMP_Description`. If there is no row, try `find_rows("Tickers", "Stooq", "<TICKER>")` for GPW names (the report's ticker may be wrong); if still nothing, say `not in Global.db` for that name and rely on Yahoo Finance. When the DB symbol differs from your mapping, the DB wins.

**Yahoo Finance.** Write this script to a temp file and run it once with all symbols: `uv run --with yfinance python <tmp>/yf_basics.py SYM1 SYM2 ...`

```python
import json, math, sys
import yfinance as yf

def pct(a, b):
    return None if not a or not b or math.isnan(a) or math.isnan(b) else round((a / b - 1) * 100, 1)

out = []
for sym in sys.argv[1:]:
    t = yf.Ticker(sym)
    try:
        info = t.info or {}
    except Exception as e:
        info = {"_error": str(e)}
    try:
        h = t.history(period="1y", auto_adjust=True)["Close"].dropna()
    except Exception:
        h = None
    last = float(h.iloc[-1]) if h is not None and len(h) else None
    back = lambda d: None if h is None or len(h) <= d else pct(last, float(h.iloc[-1 - d]))
    ytd = None
    if h is not None and len(h):
        y = h[h.index.year == h.index[-1].year]
        ytd = pct(last, float(y.iloc[0])) if len(y) > 1 else None
    out.append({
        "symbol": sym,
        "name": info.get("longName") or info.get("shortName"),
        "exchange": info.get("fullExchangeName") or info.get("exchange"),
        "currency": info.get("currency"),
        "price": info.get("currentPrice") or info.get("regularMarketPrice") or last,
        "market_cap": info.get("marketCap"),
        "pe_trailing": info.get("trailingPE"),
        "pe_forward": info.get("forwardPE"),
        "dividend_yield_pct": info.get("dividendYield"),
        "week52_low": info.get("fiftyTwoWeekLow"),
        "week52_high": info.get("fiftyTwoWeekHigh"),
        "chg_1m_pct": back(21),
        "chg_3m_pct": back(63),
        "chg_ytd_pct": ytd,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "summary": (info.get("longBusinessSummary") or "")[:300],
        "error": info.get("_error"),
    })
print(json.dumps(out, indent=1, ensure_ascii=False))
```

A symbol that errors gets `n/a` in every Yahoo column; do not guess a number.

**Industry**: the report's sector label when the page has one, else `FMP_Industry` from the DB, else the Yahoo industry. **What it does**: the report's profile paragraph, translated, at most 25 words; fall back to `FMP_Description` or the Yahoo summary.

**Stance**: derive it from the rating and the pick status, never present it as the publisher's recommendation:

| Situation | Stance |
|---|---|
| Official Top Pick | `Top Pick – buy candidate (short term, verify)` |
| Mid-term pick | `Mid-term watch` |
| +2 | `Positive` |
| +1 | `Improving` |
| 0 | `Neutral` |
| -1 | `Deteriorating` |
| -2 | `Negative – avoid` |

Add the month-on-month move after the stance, e.g. `Positive (↑ from +1)`.

## 6. Output

Header lines: edition number and publication date, the Top Picks period, the market, one line with last period's Top Picks result versus the benchmark, and `Prices as of {{today}}` (the report's prices are older).

Two tables, **Top picks** and **Worst rated**, with the columns: Company | Yahoo | Industry | What it does | Rating now/prev | Stance | Price (ccy) | Mkt cap | P/E ttm/fwd | 52w low–high | 1M / 3M / YTD | Page. Market cap in billions with the currency. Under each table, one bullet per company: `Why (StockScan): …`, one or two sentences from the page's commentary, in English.

Then **Notable moves** as bullets (name, from → to, one clause why). Close with one line: StockScan ratings are newsflow-sentiment ratings, not investment recommendations, and this summary is not investment advice.

Rules: never invent a number, write `n/a` when a value is missing; keep company names as printed in the report; cite the page number for every name; for the `us` market include the non-US listings of the foreign section and let the Yahoo suffix show the exchange.
