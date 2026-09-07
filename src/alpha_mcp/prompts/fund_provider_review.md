+++
name = "fund_provider_review"
title = "Fund provider review"
description = "Resolve identifiers, documents and index details for every fund of one provider in FundsDataSets, store the documents, and update the database."

[[arguments]]
name = "provider"
description = "FundProvider value, e.g. 'ETC Group Diversified Crypto ETP'"
required = true
+++
# TASK

Find details for each fund of provider **{{provider}}** (column FundProvider; can be an equity fund, a crypto fund or a crypto index) and update the database. Today is {{today}}.

## Data

1. `find_rows("PromptProviderFunds", "Name", "{{provider}}")` gives the provider's source page, comments and notes.
2. `find_rows("FundsDataSets", "FundProvider", "{{provider}}", columns=["ID","FundTicker","FundTickerUnique","FundName","FundUrl","FundFactSheetUrl","FundProspectusUrl","IndexDocumentationUrl","ISIN","CUSIP","CINS","SEDOL","LEI","RIC","BBGID","FIGI","MIC","InceptionDate","FundClosed","FundClosedDate","FundDomicile","AssetClass","FundType","FundRebalancingFrequency","IndexName","IndexRebalancingFrequency","IndexReconstructionFrequency","Processed","Notes","DataSet"])` lists the funds. Fetch `ChatGPTResponse` for a fund separately with `get_row("FundsDataSets", "<ID>")` when you need it.

## For each fund

**Research.** First review the fund page, the fund fact sheet and the prospectus. Cross-check with the most reliable other sources (exchange listings, index provider, regulator filings). For closed funds with no live page, search the Internet Archive; use what you find, but never put Internet Archive links into the database (the tools reject them).

**Documents.** Call `list_materials(<ID>)` to see what is already stored, then `save_material(<ID>, <url>)` for every document relevant to *this* fund: fact sheet, prospectus / base prospectus and final terms, KID, index methodology. Only documents about this particular fund. Existing documents are never overwritten: the tool reports `duplicate` for identical content and stores changed content under a dated name. Count the `new` results per fund for the summary.

**Update.** Call `update_fund(<ID>, {...}, sources=[...])` with only the fields you are 100% sure of. Fields to check and update:

CUSIP, CINS, ISIN, SEDOL, LEI, RIC, BBGID, FIGI, MIC, FundUrl (if missing or outdated), FundFactSheetUrl, FundProspectusUrl, IndexDocumentationUrl (resolve from the fact sheet or prospectus), FundName (if outdated), InceptionDate, FundClosedDate (only if the fund is closed and the date is missing), FundDomicile, AssetClass, FundType, FundRebalancingFrequency, IndexName, IndexRebalancingFrequency, IndexReconstructionFrequency, ChatGPTResponse, ChatGPTLastUpdate, Processed, Notes.

Rules the tool enforces, so plan for them: dates are `YYYY.MM.DD`; identifiers are checksum-validated; `ChatGPTResponse` must keep the existing key schema (call `chatgpt_response_schema()` first and fill every key); `ChatGPTLastUpdate` is set automatically when `ChatGPTResponse` changes. Any other column (ID, FundProvider, DataSet, FundTicker, FundClosed, FundThesis, Downloader, FMP/ETF.com fields, AuxiliaryData, ...) is rejected. Use `Notes` for dates that do not fit elsewhere: when a fund was halted, closed and liquidated (those dates are almost always different); notes are appended, never replaced. Set `Processed` to 1 when the fund is done.

## Finish

Call `finish_provider_review("{{provider}}")` to stamp the provider's Last Review Date. Do not touch Review Finished; it belongs to the manual provider review.

All updates went to the local replica and are queued in a pending SQL file for the origin database; say so in the summary. Then write the summary: per fund, which fields you resolved (with the source), which you could not resolve and why, and how many new documents were downloaded.
