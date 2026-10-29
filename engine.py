"""
engine.py - every calculation and every pass/fail decision lives here.

Design rule for the whole app:  AI READS AND EXPLAINS - THIS FILE CALCULATES AND DECIDES.
Nothing in this file calls the AI. Every rule is plain if-then logic (Lab 1 style) with
the regulation it comes from written next to it, so a lawyer can check it.

Money: the Fed's FR Y-9C data is reported in THOUSANDS of US dollars. Internally this file
keeps thousands; the helper `bn()` turns a value into billions for display.
"""

import csv
import io

# ======================================================================================
# 1. READING THE FED'S FR Y-9C BULK DATA FILE
#    (Downloaded from the FFIEC National Information Center -> "Financial Data Download")
#    Each column is an MDRM code - the Fed's ID for one line of the report.
# ======================================================================================
FIELDS = {
    # our name           (MDRM code(s),           plain-English label,                        Y-9C location)
    "total_assets":       (["BHCK2170"],          "Total assets",                             "Sch. HC, item 12"),
    "avg_assets_lev":     (["BHCAA224"],          "Average assets for leverage ratio",        "Sch. HC-R Pt I"),
    "cet1":               (["BHCAP859"],          "Common equity tier 1 (CET1) capital",      "Sch. HC-R Pt I"),
    "tier1":              (["BHCA8274"],          "Tier 1 capital",                           "Sch. HC-R Pt I"),
    "tier2":              (["BHCA5311"],          "Tier 2 capital",                           "Sch. HC-R Pt I"),
    "total_capital":      (["BHCA3792"],          "Total capital",                            "Sch. HC-R Pt I"),
    "rwa":                (["BHCAA223"],          "Risk-weighted assets",                     "Sch. HC-R Pt II"),
    "cet1_ratio":         (["BHCAP793"],          "CET1 ratio (reported, %)",                 "Sch. HC-R Pt I"),
    "tier1_ratio":        (["BHCA7206"],          "Tier 1 ratio (reported, %)",               "Sch. HC-R Pt I"),
    "total_ratio":        (["BHCA7205"],          "Total capital ratio (reported, %)",        "Sch. HC-R Pt I"),
    "leverage_ratio":     (["BHCA7204"],          "Leverage ratio (reported, %)",             "Sch. HC-R Pt I"),
    "goodwill":           (["BHCK3163"],          "Goodwill",                                 "Sch. HC-M"),
    "equity":             (["BHCK3210"],          "Total holding company equity",             "Sch. HC, item 27a"),
    "preferred":          (["BHCK3283"],          "Perpetual preferred stock",                "Sch. HC, item 23"),
    "sub_debt":           (["BHCK4062"],          "Subordinated notes and debentures",        "Sch. HC, item 19a"),
    "total_liabilities":  (["BHCK2948"],          "Total liabilities",                        "Sch. HC, item 21"),
    "deposits":           (["BHDM6631", "BHDM6636", "BHFN6631", "BHFN6636"], "Total deposits (domestic + foreign offices)", "Sch. HC, item 13"),
    "aoci":               (["BHCKB530"],          "Accumulated other comprehensive income",   "Sch. HC, item 26b"),
    "aoci_opt_out":       (["BHCAP838"],          "AOCI opt-out election (1 = yes)",          "Sch. HC-R Pt I"),
}
RATIO_FIELDS = {"cet1_ratio", "tier1_ratio", "total_ratio", "leverage_ratio", "aoci_opt_out"}


def _num(value):
    try:
        return float(value) if value not in ("", None) else None
    except ValueError:
        return None


def parse_y9c_bulk(file_bytes):
    """Read one FR Y-9C bulk file (caret '^' separated). Returns (quarter_date, {rssd_id: bank})."""
    text = file_bytes.decode("latin-1")
    reader = csv.reader(io.StringIO(text), delimiter="^")
    header = next(reader)
    banks, quarter = {}, None
    for row in reader:
        if len(row) < 10:
            continue
        rec = dict(zip(header, row))
        quarter = rec.get("RSSD9999", quarter)
        bank = {
            "rssd": rec.get("RSSD9001"),
            "name": (rec.get("RSSD9017") or "").strip(),
            "city": (rec.get("RSSD9130") or "").strip().title(),
            "state": (rec.get("RSSD9200") or "").strip(),
            "quarter": rec.get("RSSD9999"),
            "source_codes": {},
        }
        for key, (codes, _label, _loc) in FIELDS.items():
            values = [_num(rec.get(c)) for c in codes]
            present = [v for v in values if v is not None]
            bank[key] = sum(present) if present else None
            bank["source_codes"][key] = " + ".join(codes)
        if bank["total_assets"]:  # skip mid-tier entities that report no balance sheet
            banks[bank["rssd"]] = bank
    return quarter, banks


def merge_quarters(parsed_files):
    """parsed_files = [(quarter, banks), ...]. Returns {rssd: {"latest": bank, "history": [bank,...]}}."""
    merged = {}
    for quarter, banks in sorted(parsed_files, key=lambda x: x[0] or ""):
        for rssd, bank in banks.items():
            merged.setdefault(rssd, {"history": []})["history"].append(bank)
    for rssd, entry in merged.items():
        entry["latest"] = entry["history"][-1]
        assets = [b["total_assets"] for b in entry["history"] if b["total_assets"]]
        entry["avg_assets"] = sum(assets) / len(assets)
        entry["quarters"] = len(assets)
    return merged


def cross_checks(bank):
    """Code-level data integrity checks: recompute each reported ratio from its parts."""
    checks = []

    def pair(label, num, den, reported):
        if None in (bank.get(num), bank.get(den), bank.get(reported)) or not bank.get(den):
            checks.append({"check": label, "result": "Can't tell (missing data)", "ok": None})
            return
        calc = 100 * bank[num] / bank[den]
        ok = abs(calc - bank[reported]) < 0.05
        checks.append({"check": label, "result": f"Recomputed {calc:.2f}% vs reported {bank[reported]:.2f}%", "ok": ok})

    pair("CET1 ratio = CET1 / RWA", "cet1", "rwa", "cet1_ratio")
    pair("Tier 1 ratio = Tier 1 / RWA", "tier1", "rwa", "tier1_ratio")
    pair("Total capital ratio = Total capital / RWA", "total_capital", "rwa", "total_ratio")
    pair("Leverage ratio = Tier 1 / average assets", "tier1", "avg_assets_lev", "leverage_ratio")
    if bank.get("tier1") is not None and bank.get("tier2") is not None and bank.get("total_capital") is not None:
        ok = abs(bank["tier1"] + bank["tier2"] - bank["total_capital"]) <= 2
        checks.append({"check": "Tier 1 + Tier 2 = Total capital", "result": "Adds up" if ok else "Does not add up", "ok": ok})
    return checks


def bn(thousands, dp=1):
    """Thousands of dollars -> '$12.3bn' text."""
    if thousands is None:
        return "n/a"
    return f"${thousands / 1e6:,.{dp}f}bn"


# ======================================================================================
# 2. CATEGORY DETERMINATION  (12 CFR 252.5 and 12 CFR 217.2)  - pure if-then logic
# ======================================================================================
CURRENT_THRESHOLDS = {"floor": 100e6, "cat3_size": 250e6, "cat2_size": 700e6, "indicator": 75e6}  # thousands


def determine_category(avg_assets, indicators=None, gsib=False, t=CURRENT_THRESHOLDS):
    """
    avg_assets  - average total consolidated assets (thousands)
    indicators  - dict of optional FR Y-15 risk indicators (thousands) or None if unknown:
                  cross_jurisdictional, nonbank_assets, wstwf, off_balance_sheet
    Returns a dict: category, plain-English reason, missing indicators, citation.
    """
    ind = indicators or {}
    missing = [k for k in ("cross_jurisdictional", "nonbank_assets", "wstwf", "off_balance_sheet") if ind.get(k) is None]
    cite = "12 CFR 252.5; 12 CFR 217.2"
    label = {"cross_jurisdictional": "cross-jurisdictional activity", "nonbank_assets": "nonbank assets",
             "wstwf": "weighted short-term wholesale funding", "off_balance_sheet": "off-balance-sheet exposure"}

    if gsib:
        return {"category": "I", "reason": "Identified as a U.S. GSIB.", "missing": [], "cite": cite + "; 12 CFR 217.402"}
    if avg_assets < t["floor"]:
        return {"category": "None", "reason": f"Average total assets {bn(avg_assets)} are below the {bn(t['floor'], 0)} line, "
                "so the firm is outside Categories I-IV.", "missing": [], "cite": cite}
    # Category II: $700bn size, or $75bn cross-jurisdictional activity (with $100bn+ assets)
    if avg_assets >= t["cat2_size"]:
        return {"category": "II", "reason": f"Average total assets {bn(avg_assets)} are at or above {bn(t['cat2_size'], 0)}.", "missing": missing, "cite": cite}
    if (ind.get("cross_jurisdictional") or 0) >= t["indicator"]:
        return {"category": "II", "reason": f"Cross-jurisdictional activity is at or above {bn(t['indicator'], 0)}.", "missing": missing, "cite": cite}
    # Category III: $250bn size, or any of three indicators >= $75bn
    if avg_assets >= t["cat3_size"]:
        return {"category": "III", "reason": f"Average total assets {bn(avg_assets)} are at or above {bn(t['cat3_size'], 0)}.", "missing": missing, "cite": cite}
    for k in ("nonbank_assets", "wstwf", "off_balance_sheet"):
        if (ind.get(k) or 0) >= t["indicator"]:
            return {"category": "III", "reason": f"Size is under {bn(t['cat3_size'], 0)}, but {label[k]} is at or above "
                    f"{bn(t['indicator'], 0)}, which pushes the firm up to Category III.", "missing": missing, "cite": cite}
    reason = f"Average total assets {bn(avg_assets)} are between {bn(t['floor'], 0)} and {bn(t['cat3_size'], 0)}"
    reason += " and no risk indicator reaches the $75bn trigger." if not missing else \
        ". Some risk indicators were not provided, so a higher category cannot be ruled out."
    return {"category": "IV", "reason": reason, "missing": missing, "cite": cite}


# What each category switches on. Every row is tied to a provision in the library.
REQUIREMENTS = [
    # (requirement, {category: what applies}, citation)
    ("Minimum risk-based ratios: CET1 4.5%, Tier 1 6%, Total 8%",
     {"None": "Applies", "IV": "Applies", "III": "Applies", "II": "Applies", "I": "Applies"}, "12 CFR 217.10(a)"),
    ("Tier 1 leverage ratio 4%",
     {"None": "Applies", "IV": "Applies", "III": "Applies", "II": "Applies", "I": "Applies"}, "12 CFR 217.10(a)(1)(iv)"),
    ("Capital conservation buffer (2.5%) / stress capital buffer",
     {"None": "2.5% conservation buffer", "IV": "Stress capital buffer (min 2.5%)", "III": "Stress capital buffer (min 2.5%)",
      "II": "Stress capital buffer (min 2.5%)", "I": "Stress capital buffer + GSIB surcharge"}, "12 CFR 217.11(a); 12 CFR 225.8"),
    ("Annual capital plan (CCAR)",
     {"None": "No", "IV": "Yes", "III": "Yes", "II": "Yes", "I": "Yes"}, "12 CFR 225.8 ($100bn asset threshold)"),
    ("Supervisory stress test",
     {"None": "No", "IV": "Every other year", "III": "Annual", "II": "Annual", "I": "Annual"}, "12 CFR 252.44"),
    ("Company-run stress test",
     {"None": "No", "IV": "No", "III": "Yes (periodic)", "II": "Yes", "I": "Yes"}, "12 CFR 252.54"),
    ("Supplementary leverage ratio 3%",
     {"None": "No", "IV": "No", "III": "Yes", "II": "Yes", "I": "Yes (enhanced SLR for GSIBs)"}, "12 CFR 217.10(a)(1)(v)"),
    ("Countercyclical capital buffer",
     {"None": "No", "IV": "No", "III": "Yes", "II": "Yes", "I": "Yes"}, "12 CFR 217.11(b)"),
    ("AOCI opt-out available (unrealised securities gains/losses kept out of CET1)",
     {"None": "Yes", "IV": "Yes", "III": "Yes", "II": "No - AOCI flows into CET1", "I": "No - AOCI flows into CET1"},
     "12 CFR 217.22(b)(2); proposed change: Fed Board memo (19 Mar 2026)"),
    ("Liquidity coverage ratio (LCR) / NSFR",
     {"None": "No", "IV": "Reduced, only if wSTWF >= $50bn", "III": "Reduced (85%) or full if wSTWF >= $75bn",
      "II": "Full (100%)", "I": "Full (100%)"}, "12 CFR 249.30(c) (outflow adjustment percentage table)"),
    ("Internal liquidity stress tests and liquidity buffer",
     {"None": "No", "IV": "Yes", "III": "Yes", "II": "Yes", "I": "Yes"}, "12 CFR Part 252, Subpart D"),
]

CATEGORY_ORDER = ["None", "IV", "III", "II", "I"]


def requirements_for(category):
    return [{"Requirement": r, "Applies": m.get(category, "?"), "Source": c} for r, m, c in REQUIREMENTS]


def newly_applying(before_cat, after_cat):
    """Rows whose answer changes between two categories - 'what the deal switches on'."""
    rows = []
    for r, m, c in REQUIREMENTS:
        if m.get(before_cat) != m.get(after_cat):
            rows.append({"Requirement": r, "Before (acquirer alone)": m.get(before_cat), "After the deal": m.get(after_cat), "Source": c})
    return rows


def next_line(avg_assets, t=CURRENT_THRESHOLDS):
    for name, line in (("$100bn (Category IV)", t["floor"]), ("$250bn (Category III)", t["cat3_size"]), ("$700bn (Category II)", t["cat2_size"])):
        if avg_assets < line:
            return name, line - avg_assets
    return None, None


# ======================================================================================
# 3. PRO FORMA CAPITAL AFTER CLOSING  (purchase accounting, simplified)
#    Goodwill is deducted from CET1 - 12 CFR 217.22(a)(1).
# ======================================================================================
def pro_forma(acq, tgt, price, stock_pct, fv_mark=0.0):
    """
    acq, tgt  - bank dicts (thousands)
    price     - purchase price for the target's common equity (thousands)
    stock_pct - share of the price paid in new acquirer shares (0-100); the rest is cash
    fv_mark   - fair-value write-down of the target's assets at closing (thousands, positive = loss)

    Simplified mechanics (stated so a reader can check them):
      target tangible common equity (TCE) = equity - preferred - goodwill - fair-value mark
      new goodwill   = price - target TCE   (never below zero)
      new CET1       = acquirer CET1 + new shares issued - new goodwill
                       (target's own CET1 is replaced by the price paid; cash leaves the group)
      AT1 and Tier 2 = carried over from both banks (target's preferred and sub debt assumed)
      RWA            = acquirer RWA + target RWA (cash paid out carries a 0% risk weight)
      leverage base  = both banks' average assets - cash paid - fair-value mark
    """
    stock = price * stock_pct / 100
    cash = price - stock
    tgt_tce = (tgt["equity"] or 0) - (tgt["preferred"] or 0) - (tgt["goodwill"] or 0) - fv_mark
    new_goodwill = max(0.0, price - tgt_tce)
    cet1 = acq["cet1"] + stock - new_goodwill
    at1 = (acq["tier1"] - acq["cet1"]) + (tgt["tier1"] - tgt["cet1"])
    tier1 = cet1 + at1
    total = tier1 + (acq["tier2"] or 0) + (tgt["tier2"] or 0)
    rwa = acq["rwa"] + tgt["rwa"]
    lev_base = acq["avg_assets_lev"] + tgt["avg_assets_lev"] - cash - fv_mark
    total_assets = acq["total_assets"] + tgt["total_assets"] - cash - fv_mark - (tgt["goodwill"] or 0) + new_goodwill
    return {
        "price": price, "stock": stock, "cash": cash, "target_tce": tgt_tce, "new_goodwill": new_goodwill,
        "cet1": cet1, "tier1": tier1, "total_capital": total, "rwa": rwa, "lev_base": lev_base,
        "total_assets": total_assets,
        "cet1_ratio": 100 * cet1 / rwa, "tier1_ratio": 100 * tier1 / rwa, "total_ratio": 100 * total / rwa,
        "leverage_ratio": 100 * tier1 / lev_base,
        "deposits": (acq["deposits"] or 0) + (tgt["deposits"] or 0),
        "total_liabilities": (acq["total_liabilities"] or 0) + (tgt["total_liabilities"] or 0) + cash * 0,
    }


def capital_requirements(category, scb=2.5):
    """Minimum + buffer each ratio must clear to avoid payout limits (12 CFR 217.10, 217.11)."""
    buffer = 2.5 if category == "None" else max(2.5, scb)
    return {
        "cet1_ratio": (4.5, 4.5 + buffer),
        "tier1_ratio": (6.0, 6.0 + buffer),
        "total_ratio": (8.0, 8.0 + buffer),
        "leverage_ratio": (4.0, 4.0),
        "buffer": buffer,
        "buffer_name": "capital conservation buffer" if category == "None" else "stress capital buffer",
    }


def capital_tests(pf, category, scb=2.5, slr_exposure=None):
    req = capital_requirements(category, scb)
    names = {"cet1_ratio": "CET1 ratio", "tier1_ratio": "Tier 1 ratio", "total_ratio": "Total capital ratio", "leverage_ratio": "Tier 1 leverage ratio"}
    rows = []
    for key, label in names.items():
        minimum, with_buffer = req[key]
        v = pf[key]
        if v < minimum:
            status = "not_met"
        elif v < with_buffer:
            status = "warning"
        else:
            status = "met"
        rows.append({"test": label, "value": v, "minimum": minimum, "with_buffer": with_buffer, "status": status,
                     "cite": "12 CFR 217.10(a); 12 CFR 217.11(a)"})
    if category in ("III", "II", "I"):
        if slr_exposure:
            v = 100 * pf["tier1"] / slr_exposure
            rows.append({"test": "Supplementary leverage ratio", "value": v, "minimum": 3.0, "with_buffer": 3.0,
                         "status": "met" if v >= 3 else "not_met", "cite": "12 CFR 217.10(a)(1)(v)"})
        else:
            rows.append({"test": "Supplementary leverage ratio", "value": None, "minimum": 3.0, "with_buffer": 3.0,
                         "status": "unknown", "cite": "12 CFR 217.10(a)(1)(v)"})
    return rows


def price_sweep(acq, tgt, stock_pct, fv_mark, category, scb, points=60):
    """CET1 ratio across a range of prices (for the slider chart), plus the break-even price."""
    tce = (tgt["equity"] or 0) - (tgt["preferred"] or 0) - (tgt["goodwill"] or 0) - fv_mark
    lo, hi = max(tce * 0.25, 1), max(tce * 3.5, 2)
    need = capital_requirements(category, scb)["cet1_ratio"][1]
    xs, ys, breakeven = [], [], None
    for i in range(points + 1):
        p = lo + (hi - lo) * i / points
        r = pro_forma(acq, tgt, p, stock_pct, fv_mark)["cet1_ratio"]
        xs.append(p)
        ys.append(r)
        if breakeven is None and r < need:
            breakeven = p
    return xs, ys, need, breakeven


# ======================================================================================
# 4. STATUTORY CAPS
# ======================================================================================
def deposit_cap(combined_deposits, us_total_deposits):
    """12 U.S.C. 1842(d)(2)(A) and 1828(c)(13): no more than 10% of U.S. insured-institution deposits."""
    if not us_total_deposits:
        return {"status": "unknown", "share": None, "detail": "Enter total U.S. deposits to run this test."}
    share = 100 * combined_deposits / us_total_deposits
    return {"status": "met" if share <= 10 else "not_met", "share": share,
            "detail": f"Combined deposits are {share:.2f}% of the U.S. total (cap: 10%)."}


def liabilities_cap(combined_liabilities, aggregate_fin_liabilities):
    """12 U.S.C. 1852(b); 12 CFR 251: no more than 10% of aggregate financial-sector liabilities."""
    if not aggregate_fin_liabilities:
        return {"status": "unknown", "share": None, "detail": "Enter the Fed's published financial-sector liabilities figure to run this test."}
    share = 100 * combined_liabilities / aggregate_fin_liabilities
    return {"status": "met" if share <= 10 else "not_met", "share": share,
            "detail": f"Combined liabilities are {share:.2f}% of financial-sector liabilities (cap: 10%)."}


def approval_path(structure, charter):
    """Which statute and agency are engaged - simplified overview, not a full analysis."""
    rows = []
    if structure in ("Holding company buys holding company", "Both"):
        rows.append({"Approval": "Federal Reserve Board", "Statute": "BHC Act s.3 - 12 U.S.C. 1842",
                     "Why": "A bank holding company acquiring control of another bank holding company or bank."})
    if structure in ("Bank merges into bank", "Both"):
        agency = {"National bank (OCC)": "OCC", "State member bank (Fed)": "Federal Reserve Board",
                  "State non-member bank (FDIC)": "FDIC"}.get(charter, "?")
        rows.append({"Approval": agency, "Statute": "Bank Merger Act - 12 U.S.C. 1828(c)",
                     "Why": "Insured banks merging; the agency is set by the resulting bank's charter."})
    return rows


# ======================================================================================
# 5. CROSS-BORDER (Canadian parent buying a U.S. bank)
# ======================================================================================
def ihc_test(global_assets, us_nonbranch_assets, before_nonbranch=None):
    """12 CFR 252.153: FBO with $100bn+ U.S. assets and $50bn+ U.S. non-branch assets must form a U.S. IHC.
    (Global and combined U.S. assets are also relevant; this is the headline screen.)"""
    if global_assets is None or us_nonbranch_assets is None:
        return {"status": "unknown", "detail": "Enter the parent's global assets and U.S. non-branch assets."}
    required = global_assets >= 100e6 and us_nonbranch_assets >= 50e6
    already = required and before_nonbranch is not None and before_nonbranch >= 50e6
    return {"status": "applies" if already else ("newly_applies" if required else "met"),
            "required": required,
            "detail": (f"U.S. non-branch assets after the deal: {bn(us_nonbranch_assets)} "
                       f"({'at or above' if us_nonbranch_assets >= 50e6 else 'below'} the $50bn IHC trigger).")}


OSFI_TARGETS = {"cet1": 8.0, "tier1": 9.5, "total": 11.5, "leverage": 3.5}  # D-SIB, before DSB; CAR Ch.1 s.10; LR Guideline


def parent_pro_forma(parent, deal_goodwill, target_rwa, target_assets, stock, cash, dsb=3.0, dsib=True):
    """Parent (OSFI) ratios after buying the U.S. target. All inputs in the parent's currency (millions).
    Simplified: goodwill is deducted from CET1 (OSFI CAR Ch.2); the target's RWA is added at its U.S.
    figure (Canadian risk weights may differ); leverage exposure grows by target assets less cash paid."""
    cet1 = parent["cet1"] + stock - deal_goodwill
    tier1 = parent["tier1"] + stock - deal_goodwill
    rwa = parent["rwa"] + target_rwa
    exposure = parent["leverage_exposure"] + target_assets - cash
    base = OSFI_TARGETS if dsib else {"cet1": 7.0, "tier1": 8.5, "total": 10.5, "leverage": 3.0}
    return {
        "cet1_ratio_before": 100 * parent["cet1"] / parent["rwa"],
        "tier1_ratio_before": 100 * parent["tier1"] / parent["rwa"],
        "leverage_before": 100 * parent["tier1"] / parent["leverage_exposure"],
        "cet1_ratio_after": 100 * cet1 / rwa,
        "tier1_ratio_after": 100 * tier1 / rwa,
        "leverage_after": 100 * tier1 / exposure,
        "cet1_target": base["cet1"] + (dsb if dsib else 0),
        "tier1_target": base["tier1"] + (dsb if dsib else 0),
        "leverage_target": base["leverage"],
    }


# ======================================================================================
# 6. SUMMARY TABLE (met / not met / newly applies / can't tell)
# ======================================================================================
STATUS_LABEL = {"applies": "ℹ️ Already applies", "met": "✅ Met", "not_met": "❌ Not met", "newly_applies": "⚠️ Newly applies",
                "warning": "⚠️ Inside buffer", "unknown": "❔ Can't tell"}
