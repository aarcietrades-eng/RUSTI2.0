"""RUSTI web chat interface — Streamlit.
Runs independently of the Discord bot. Uses only prompts.py + openai.
"""
import os
import streamlit as st
from openai import AsyncOpenAI

from prompts import RUSTI_CHAT_PROMPT

# ---------------------------------------------------------------------------
# Config — reads from Streamlit secrets first, falls back to env vars
# ---------------------------------------------------------------------------
def _get_secret(key, default=None):
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.getenv(key, default)

GROQ_API_KEY = _get_secret("GROQ_API_KEY")
GROQ_MODEL = _get_secret("GROQ_MODEL", "openai/gpt-oss-20b")

if not GROQ_API_KEY:
    st.error(
        "**GROQ_API_KEY is not configured.** Add it in "
        "Streamlit Cloud → your app → Settings → Secrets."
    )
    st.stop()

client = AsyncOpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
    timeout=60.0,
)

# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="RUSTI",
    page_icon="🦉",
    layout="wide",
)

st.title("🦉 RUSTI")
st.caption(
    "Risk Understanding & Speculative Token Intelligence — "
    "ask about Solana memecoins, NFTs, or crypto concepts. "
    "Not financial advice."
)

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Ask RUSTI anything about crypto…"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("RUSTI is thinking…"):
            import asyncio

            async def _call():
                messages = [{"role": "system", "content": RUSTI_CHAT_PROMPT}]
                for m in st.session_state.messages:
                    messages.append({"role": m["role"], "content": m["content"]})
                resp = await client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=messages,
                    max_tokens=700,
                    temperature=0.5,
                )
                return (resp.choices[0].message.content or "").strip()

            try:
                answer = asyncio.run(_call())
            except Exception as e:
                answer = f"**Error:** `{type(e).__name__}: {e}`"

        st.markdown(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})
