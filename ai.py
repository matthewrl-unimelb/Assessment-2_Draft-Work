"""
ai.py - every AI call in the app goes through this file.

Design rule:  AI READS AND EXPLAINS - engine.py CALCULATES AND DECIDES.
So the AI is never asked to do arithmetic or decide pass/fail. It is given:
  * facts the app already computed (numbers, categories, test results), and
  * passages retrieved from the closed-world regulation library (library.py),
and asked to explain, flag or draft - citing the passages by their ID, e.g. [C41].

"Closed world" is enforced in four layers:
  1. Retrieve first: only library passages are ever sent to the AI.
  2. Instructions: answer ONLY from those passages, otherwise say so.
  3. Code check: every [C#] citation must be one of the passages actually sent.
  4. Relevance floor: if the library has nothing relevant, the AI is not called at all.
Plus (Lab 2) a REFLECTION step: a second AI call checks the answer against its passages,
and if it fails, the app LOOPS - rewrites the search, retrieves again and retries.

Providers: Anthropic (Claude Haiku - cheap, for testing) or OpenAI (for submission).
No web access is ever switched on for either provider.
"""

import json
import os
import re
import time

import streamlit as st

DEFAULT_MODELS = {"Anthropic (Claude)": "claude-haiku-5-5", "OpenAI": "gpt-4o-mini"}
RELEVANCE_FLOOR = 4.0     # minimum BM25 score for the best passage before the AI is called
MAX_LOOPS = 2             # reflection retries for the question box

CLOSED_WORLD_RULES = (
    "You are an assistant to a U.S./Canadian banking M&A lawyer. Rules you must follow:\n"
    "1. Use ONLY the numbered passages and the APP FACTS provided in this message. Do not use outside "
    "knowledge, do not mention cases, rules or numbers that are not in them.\n"
    "2. After every sentence that relies on a passage, cite it in square brackets with its ID, e.g. [C41].\n"
    "3. Never do arithmetic and never decide pass/fail yourself - the APP FACTS already contain the "
    "computed numbers and results; repeat them, do not change them.\n"
    "4. If the passages do not answer something, say plainly: 'The loaded regulations do not cover this.'\n"
    "5. Plain English for a busy senior lawyer. No preamble."
)


# --------------------------------------------------------------------------------------
# Session bookkeeping: call counter and audit trail (shown in the sidebar and exported)
# --------------------------------------------------------------------------------------
def _log(step, purpose, prompt, output, passages=None, seconds=0.0):
    st.session_state.setdefault("audit", []).append({
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "step": step, "purpose": purpose,
        "passages_sent": [p["id"] + " " + p["cite"] for p in (passages or [])],
        "prompt_chars": len(prompt), "output": output, "seconds": round(seconds, 1),
    })
    st.session_state["ai_calls"] = st.session_state.get("ai_calls", 0) + 1


def ready():
    return bool(st.session_state.get("api_key")) or os.environ.get("APP_DEV_MOCK") == "1"


# --------------------------------------------------------------------------------------
# The one function that actually talks to an AI provider
# --------------------------------------------------------------------------------------
def chat(system, user, step, purpose, passages=None, max_tokens=900, want_json=False):
    provider = st.session_state.get("provider", "Anthropic (Claude)")
    model = st.session_state.get("model") or DEFAULT_MODELS[provider]
    key = st.session_state.get("api_key")
    started = time.time()

    if os.environ.get("APP_DEV_MOCK") == "1" and not key:
        text = _mock(user, passages, want_json)
    elif provider == "OpenAI":
        from openai import OpenAI
        client = OpenAI(api_key=key)
        kwargs = {"response_format": {"type": "json_object"}} if want_json else {}
        resp = client.chat.completions.create(
            model=model, max_completion_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], **kwargs)
        text = resp.choices[0].message.content or ""
    else:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        # Low effort: extended "thinking" is switched off. Otherwise the model can spend the whole
        # token allowance thinking and return no text at all (found in live testing).
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, system=system, thinking={"type": "disabled"},
            messages=[{"role": "user", "content": user}])
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    if not text.strip():
        text = "The AI returned an empty answer. Press the step again or try a shorter question."

    _log(step, purpose, system + user, text, passages, time.time() - started)
    return text


def _parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def _passages_block(passages):
    return "\n\n".join(f"[{p['id']}] {p['cite']} - {p['title']} (status: {p['status']})\n{p['text'][:2200]}" for p in passages)


def check_citations(text, passages):
    """Layer 3 of closed world: every [C#] must be a passage we actually sent."""
    sent = {p["id"] for p in passages}
    cited = set(re.findall(r"\[(C\d+)\]", text))
    return {"cited": sorted(cited), "invalid": sorted(cited - sent), "has_citations": bool(cited)}


# --------------------------------------------------------------------------------------
# 1. Grounded explanation for a page (one AI call + code citation check)
# --------------------------------------------------------------------------------------
def explain(step, task, facts, query, lib, k=6):
    passages = lib.search(query, k=k)
    if not passages or passages[0]["score"] < RELEVANCE_FLOOR:
        return {"text": "The loaded regulations do not contain material relevant to this step.", "passages": [], "check": None}
    user = (f"TASK: {task}\n\nAPP FACTS (computed by the app - do not change):\n{facts}\n\n"
            f"PASSAGES:\n{_passages_block(passages)}")
    text = chat(CLOSED_WORLD_RULES, user, step, task[:80], passages)
    check = check_citations(text, passages)
    if check["invalid"]:
        text = _strip_invalid(text, check["invalid"])
    return {"text": text, "passages": passages, "check": check}


def _strip_invalid(text, invalid):
    for cid in invalid:
        text = text.replace(f"[{cid}]", "[citation removed - not in sources]")
    return text


# --------------------------------------------------------------------------------------
# 2. Reflection (Lab 2): does each sentence follow from the cited passages?
# --------------------------------------------------------------------------------------
def reflect(step, answer, passages, question):
    user = (
        "You are a strict checker. Compare the ANSWER with the PASSAGES it cites.\n"
        "Return JSON only: {\"supported\": true/false, \"answered\": true/false, \"unsupported\": [\"sentence\", ...], "
        "\"better_search\": \"keywords likely to appear in the regulation text that would answer the question, "
        "e.g. section topics and defined terms\"}.\n"
        "supported = every statement is backed by its cited passage (saying the regulations do not cover something "
        "counts as supported). answered = the passages actually answered the question asked.\n\n"
        f"QUESTION: {question}\n\nANSWER:\n{answer}\n\nPASSAGES:\n{_passages_block(passages)}")
    verdict = _parse_json(chat("Return only valid JSON.", user, step, "Reflection check", passages, max_tokens=400, want_json=True))
    verdict.setdefault("supported", False)
    verdict.setdefault("answered", True)
    verdict.setdefault("unsupported", [])
    verdict.setdefault("better_search", "")
    return verdict


# --------------------------------------------------------------------------------------
# 3. The question box: retrieve -> answer -> reflect -> loop (Lab 2 RAG + looping + reflection)
# --------------------------------------------------------------------------------------
def ask(step, question, facts, lib):
    query, trail, best = question, [], None
    for attempt in range(1, MAX_LOOPS + 1):
        passages = lib.search(query, k=6)
        if not passages or passages[0]["score"] < RELEVANCE_FLOOR:
            trail.append(f"Attempt {attempt}: nothing relevant found for '{query}' - AI not called.")
            return {"text": "I couldn't find that in the regulations loaded into this app.", "passages": [], "trail": trail, "verified": True}
        user = (f"QUESTION: {question}\n\nAPP FACTS about the deal on screen (computed by the app):\n{facts}\n\n"
                f"PASSAGES:\n{_passages_block(passages)}")
        answer = chat(CLOSED_WORLD_RULES, user, step, f"Q&A attempt {attempt}", passages)
        cites = check_citations(answer, passages)
        if cites["invalid"]:
            answer = _strip_invalid(answer, cites["invalid"])
        verdict = reflect(step, answer, passages, question)
        trail.append(f"Attempt {attempt}: searched '{query}', cited {', '.join(cites['cited']) or 'nothing'}; "
                     f"reflection says {'SUPPORTED' if verdict['supported'] else 'NOT SUPPORTED'}"
                     f"{'' if verdict['answered'] else ' but the passages did NOT answer the question'}.")
        ok = verdict["supported"] and not cites["invalid"]
        if ok:
            best = {"text": answer, "passages": passages, "trail": trail, "verified": True}
            if verdict["answered"]:
                return best
        # LOOP: search again with the checker's better keywords
        query = verdict["better_search"] or question + " definition requirement"
    if best:
        return best
    return {"text": answer + "\n\n_⚠️ The checker could not confirm every sentence of this answer against the regulations. "
            "Treat it as a lead, not a conclusion._", "passages": passages, "trail": trail, "verified": False,
            "unsupported": verdict.get("unsupported", [])}


# --------------------------------------------------------------------------------------
# 4. Data review of an uploaded bank (no passages - the AI only looks at the figures)
# --------------------------------------------------------------------------------------
def review_bank(step, bank_name, figures_text, checks_text):
    user = (
        "Below are figures the app read from a bank holding company's FR Y-9C filing, and the app's own "
        "arithmetic cross-checks. In at most 5 short bullet points: (1) describe the bank's size and capital "
        "position in plain English, (2) flag anything unusual a lawyer should double-check (e.g. a negative or "
        "missing figure, a large AOCI loss, heavy reliance on goodwill). Do not recompute anything, do not cite "
        "law, and do not speculate beyond the figures.\n\n"
        f"BANK: {bank_name}\nFIGURES:\n{figures_text}\n\nAPP CROSS-CHECKS:\n{checks_text}")
    return chat("You are a careful bank-regulatory analyst.", user, step, f"Data review: {bank_name}", max_tokens=450)


# --------------------------------------------------------------------------------------
# 5. Extracting figures from a PDF (e.g. a Canadian parent's Pillar 3 / capital disclosure)
# --------------------------------------------------------------------------------------
PARENT_FIELDS = {
    "total_assets": "Total assets",
    "cet1": "Common Equity Tier 1 capital",
    "tier1": "Tier 1 capital",
    "rwa": "Total risk-weighted assets",
    "leverage_exposure": "Leverage ratio exposure measure",
    "cet1_ratio": "CET1 ratio (%)",
    "leverage_ratio": "Leverage ratio (%)",
}


def extract_parent_figures(step, pdf_text):
    user = (
        "Extract these figures from the bank disclosure text below. Use the most recent quarter shown. "
        "Give amounts in MILLIONS of the report's currency and ratios as percentages. If a figure is not "
        "clearly stated, use null - never guess or calculate. Quote where you found it.\n"
        "Return JSON only: {\"currency\": \"CAD\", \"period\": \"...\", \"figures\": {"
        + ", ".join(f'"{k}": {{"value": number or null, "found_as": "quote"}}' for k in PARENT_FIELDS)
        + "}}\n\nFIELDS:\n" + "\n".join(f"- {k}: {v}" for k, v in PARENT_FIELDS.items())
        + f"\n\nDISCLOSURE TEXT:\n{pdf_text[:60000]}")
    return _parse_json(chat("Return only valid JSON.", user, step, "Extract parent capital figures", max_tokens=1200, want_json=True))


def find_capital_pages(pdf_reader, max_pages=12):
    """Code (not AI) picks the pages most likely to hold the capital table, so the AI reads less."""
    scored = []
    for i, page in enumerate(pdf_reader.pages):
        t = (page.extract_text() or "")
        low = t.lower()
        score = sum(low.count(w) for w in ("common equity tier 1", "cet1", "risk-weighted", "leverage ratio", "tier 1 capital"))
        if score:
            scored.append((score, i, t))
    scored.sort(reverse=True)
    keep = sorted(scored[:max_pages], key=lambda x: x[1])
    return "\n\n".join(f"--- page {i + 1} ---\n{t}" for _, i, t in keep)


# --------------------------------------------------------------------------------------
# Offline test mode (developer only: set APP_DEV_MOCK=1). Lets the plumbing be tested without a key.
# --------------------------------------------------------------------------------------
def _mock(user, passages, want_json):
    if want_json and "strict checker" in user:
        return json.dumps({"supported": True, "unsupported": [], "better_search": ""})
    if want_json:
        return json.dumps({"currency": "CAD", "period": "Q3 2026 (mock)", "figures": {
            "total_assets": {"value": 2000000, "found_as": "mock"}, "cet1": {"value": 95000, "found_as": "mock"},
            "tier1": {"value": 110000, "found_as": "mock"}, "rwa": {"value": 720000, "found_as": "mock"},
            "leverage_exposure": {"value": 2400000, "found_as": "mock"}, "cet1_ratio": {"value": 13.2, "found_as": "mock"},
            "leverage_ratio": {"value": 4.4, "found_as": "mock"}}})
    cite = f" [{passages[0]['id']}]" if passages else ""
    return f"(Offline test mode - no AI was called.) This is placeholder text standing in for the AI's explanation{cite}."
