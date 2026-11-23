"""PartPilot web UI:  streamlit run streamlit_app.py
Login -> role-aware chat; component engineers get a Review page, the auditor gets Audit and Data-quality pages."""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from partpilot import auth, bootstrap, llm, review
from partpilot.agent import Conversation
from partpilot.normalize import PLANTS
from partpilot.perm import Denied

st.set_page_config(page_title="PartPilot", page_icon="🧭", layout="wide")

EXAMPLES = {
    "planner": ["How many BRD-UNO3 can North Ridge build on 2027-03-15 using stock, open POs and approved substitutes?",
                "Shortage for 120 PRN-MK3S at Bay Haven on 2027-03-20 without open POs",
                "What is the average unit cost of the BRD-PICO components?"],
    "product_owner": ["Which products use the ESD bag PK-1001?",
                      "How many PRN-MK3S can Bay Haven build on 2027-03-20 including open POs?"],
    "plant_lead": ["How much RP2040 stock is available at North Ridge on 2027-03-15?",
                   "Available stock of CA-1004 at NRG on 2027-03-15, and why were lots excluded?"],
    "component_engineer": ["Can the LM1117MPX-5.0 replace the NCP1117ST50T3G on the UNO board ASM-11000?",
                           "Which substitutes are usable for CA-1001 on ASM-11000?"],
    "auditor": [],
}


@st.cache_resource
def _prepare():
    done, pw = bootstrap.ensure_data(), bootstrap.ensure_accounts()
    if pw:  # shown once in the server console only, never in the page
        print("PartPilot demo accounts created; password for every demo account (shown once):", pw, flush=True)
    return done, pw


done, generated_pw = _prepare()

st.markdown("""<style>
.block-container {padding-top: 1.6rem;}
.pp-badge {display:inline-block;padding:2px 10px;border-radius:12px;background:#e8f0fe;color:#1a3d8f;font-size:0.85rem;margin-right:6px;}
.pp-muted {color:#6b7280;font-size:0.85rem;}
</style>""", unsafe_allow_html=True)


def login_view():
    st.title("PartPilot")
    st.caption("Role-aware parts and material-planning assistant over open-hardware BOMs (Arduino UNO Rev3, Raspberry Pi Pico, "
               "Prusa i3 MK3S+). Stock, POs, prices, suppliers and plants are synthetic.")
    if generated_pw:
        st.info("Demo accounts were just created. The generated password is printed in the server console.")
    left, right = st.columns([1, 1.3])
    with left:
        with st.form("login"):
            u = st.text_input("Username", placeholder="planner")
            p = st.text_input("Password", type="password")
            ok = st.form_submit_button("Log in", width="stretch")
        if ok:
            user = auth.login(u, p)
            if user:
                st.session_state.user = user
                st.session_state.conv = Conversation(user)
                st.session_state.history = []
                st.rerun()
            st.error("Login failed.")
    with right:
        st.markdown("**Demo accounts** (one password for all, set by whoever runs the demo):")
        st.dataframe(pd.DataFrame([
            ("planner", "Supply Planner", "whole company, costs only as group averages"),
            ("po_boards / po_printers", "Product Owner", "one product line"),
            ("lead_nrg / lead_bhv", "Plant Materials Lead", "own plant's stock and POs"),
            ("eng_passives / eng_semis / ...", "Component Engineer", "own commodity group, approves substitutes"),
            ("auditor", "Auditor", "audit metadata and cleaning job, no parts data")],
            columns=["username", "role", "sees"]), hide_index=True, width="stretch")


def sidebar(user):
    with st.sidebar:
        st.markdown("### PartPilot")
        st.markdown(f"<span class='pp-badge'>{user.role_name}</span><span class='pp-badge'>{user.scope}</span>",
                    unsafe_allow_html=True)
        st.markdown(f"<div class='pp-muted'>signed in as <b>{user.username}</b><br>LLM: {llm.provider()} / {llm.model_name()}"
                    f"<br>data baseline 2027-03-01</div>", unsafe_allow_html=True)
        pages = ["Ask"]
        if user.role == "component_engineer":
            pages.append("Review")
        if user.role in ("auditor", "component_engineer"):
            pages.append("Data quality")
        if user.role == "auditor":
            pages = ["Audit", "Data quality"]
        page = st.radio("Page", pages, label_visibility="collapsed")
        st.divider()
        if st.button("New conversation", width="stretch"):
            st.session_state.conv = Conversation(user)
            st.session_state.history = []
            st.rerun()
        if st.button("Log out", width="stretch"):
            for k in ("user", "conv", "history"):
                st.session_state.pop(k, None)
            st.rerun()
        st.caption("Plants: " + "; ".join(f"{k} = {v}" for k, v in PLANTS.items()))
    return page


def ask_page(user):
    st.subheader("Ask PartPilot")
    st.caption("Every number comes from permission-checked tools; the model only picks tools and writes the answer.")
    hist = st.session_state.history
    for h in hist:
        with st.chat_message(h["role"]):
            st.markdown(h["text"])
            if h.get("tools") is not None:
                with st.expander(f"Basis: {len(h['tools'])} tool call(s) · {h['stats']}"):
                    for t in h["tools"]:
                        st.code(f"{t['tool']}({t['args']})" + ("   -> DENIED" if t.get("denied") else ""), language="text")
    q = st.chat_input("Ask about BOMs, stock, POs, substitutes, shortages ...")
    if not hist and EXAMPLES.get(user.role):
        st.markdown("<div class='pp-muted'>Try one of these:</div>", unsafe_allow_html=True)
        for i, ex in enumerate(EXAMPLES[user.role]):
            if st.button(ex, key=f"ex{i}"):
                q = ex
    if q:
        conv = st.session_state.conv
        with st.spinner("Checking the data ..."):
            try:
                ans = conv.ask(q)
            except Exception as e:  # provider down etc. - shown, never hidden
                ans = f"The LLM provider failed: {type(e).__name__}: {e}"
        s = conv.last_stats
        stats = f"{s['in_tokens']} in / {s['out_tokens']} out tokens · {s['seconds']} s · est. ${s['cost_usd']}"
        hist += [{"role": "user", "text": q}, {"role": "assistant", "text": ans, "tools": conv.last_tools, "stats": stats}]
        st.rerun()


def review_page(user):
    st.subheader(f"Review queue · {user.scope}")
    st.caption("Items wait here until you decide. Approving a pending substitute makes it usable immediately; conflicts stay "
               "excluded until the source data is fixed. Remarks are shown to you, never to the model.")
    try:
        items = review.items_for(user)
    except Denied as e:
        st.error(str(e))
        return
    if not items:
        st.success("Nothing waiting for your commodity group.")
    for p in items:
        with st.container(border=True):
            c1, c2, c3 = st.columns([4, 1, 1])
            with c1:
                st.markdown(f"**{p['id']} · {p['kind'].replace('_', ' ').lower()}** · `{p['key']}` · decision: **{p['decision']}**")
                st.caption(str(p["detail"]))
                if p.get("remark_for_humans"):
                    st.warning(f"Remark (human-only, has no approval authority): {p['remark_for_humans']}")
            if c2.button("Approve", key=f"a{p['id']}", width="stretch"):
                review.decide(user, p["id"], "approve")
                st.rerun()
            if c3.button("Reject", key=f"r{p['id']}", width="stretch"):
                review.decide(user, p["id"], "reject")
                st.rerun()


def audit_page(user):
    st.subheader("Audit log (metadata only)")
    st.caption("The auditor sees who asked, which tools ran, denials, tokens, cost and latency - never questions or answers.")
    rows = review.audit_metadata(200)
    if rows:
        df = pd.DataFrame(rows)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Questions", len(df))
        c2.metric("Denied tool calls", int(df["denied_tool_calls"].sum()))
        c3.metric("Avg latency (s)", round(df["seconds"].fillna(0).mean(), 1))
        c4.metric("Est. cost (USD)", round(df["cost_usd"].fillna(0).sum(), 4))
        st.dataframe(df.iloc[::-1], hide_index=True, width="stretch")
    else:
        st.info("No questions logged yet.")
    if st.button("Run cleaning job"):
        from partpilot import clean, engine
        r = clean.run()
        engine.reload()
        st.success(f"Cleaning done. Usable rows: { {k: v[1] for k, v in r['counts'].items()} }; pending items: {r['pending']}")


def quality_page(user):
    from partpilot import clean
    st.subheader("Data-quality report")
    if os.path.exists(clean.REPORT):
        with open(clean.REPORT, encoding="utf-8") as f:
            st.markdown(f.read())
    else:
        st.info("Run the cleaning job first.")


user = st.session_state.get("user")
if not user:
    login_view()
else:
    page = sidebar(user)
    {"Ask": ask_page, "Review": review_page, "Audit": audit_page, "Data quality": quality_page}[page](user)
