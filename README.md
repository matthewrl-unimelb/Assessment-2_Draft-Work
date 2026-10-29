# Bank Merger Regulatory Check

A Streamlit app for a banking M&A lawyer. Upload two bank holding companies' public **FR Y-9C** filings and it works through seven steps:

1. **Upload**: read the filings and cross-check the figures.
2. **Each bank**: each bank's size category and the rules that apply to it.
3. **Combined**: what the combined bank crosses and what newly applies.
4. **Capital & price**: capital after closing, with a purchase-price slider.
5. **Approvals**: the approval path, plus the 10% deposit and liabilities caps.
6. **Cross-border**: for a Canadian buyer, the U.S. IHC test and the OSFI ratios.
7. **Summary**: met / not met / newly applies / can't tell, a draft memo, and a Word export.

**Deployed app:** _add Streamlit Cloud URL here_

## Design: code decides, AI explains
| Layer | What it does | Class technique |
|---|---|---|
| `engine.py` | Every number and every pass/fail, as if-then rules with the regulation cited next to each one | Lab 1: boolean / if-then logic |
| `library.py` | Closed-world regulation library: 13 documents split into ~1,100 section-level chunks, searched with BM25 | Lab 2: retrieval (RAG) |
| `ai.py` | Explains, reviews and drafts **only** from retrieved passages. Code checks every `[C#]` citation. A second AI call reflects on the answer, and the app loops with a better search if it fails | Lab 2: RAG + looping + reflection |

The AI never browses the web, never does arithmetic and never decides a result. A full run makes **7+ AI calls**:
- 2 data reviews
- one explanation each on steps 2, 3, 5 and 6
- the memo draft, plus its reflection check

There are more on demand (the Q&A box on every step, the capital explanation, and PDF extraction).

## Test cases
_All files are in `test_data/`._

1. **Two banks under $100bn (works well).** Click **Try with example banks** (Associated Banc-Corp acquiring WaFd, Q3 2026).
   - Expected: both banks and the combined bank fall below Category IV.
   - Step 3 shows about $21bn of room before the $100bn line.
   - All capital ratios are met at 1.5× tangible book.
2. **Price stress (the slider).** Same banks. On Step 4, move the price toward 3× tangible book and add a $0.5bn fair-value write-down.
   - Expected: CET1 falls inside the minimum-plus-buffer.
   - The app reports the break-even price.
   - The Step 7 summary flips to ⚠️/❌.
3. **Canadian acquirer (shows the limits).** Turn on Step 6 and upload a Canadian bank's Supplementary Regulatory Capital Disclosure PDF.
   - Expected: the AI extracts the parent's figures with quotes showing where it found each one. Some may be missed or mis-scaled, and the user must check them.
   - The parent result uses U.S. RWA for the target, which OSFI would calibrate differently. This is a known limitation.

## Run locally
```
pip install -r requirements.txt
cp .env.example .env        # paste your API key into .env
streamlit run app.py
```
To rebuild the regulation library from PDFs: `python library.py build path/to/pdf_folder`

_Screening tool, not legal advice. Calculations are simplified; verify against the filings and rules cited._
