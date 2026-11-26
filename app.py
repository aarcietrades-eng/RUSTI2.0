import os
import streamlit as st
from openai import AsyncOpenAI
from prompts import RUSTI_CHAT_PROMPT


# ---------------------------------------------------------------------------
# Secrets
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


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="RUSTI",
    page_icon="🦉",
    layout="centered",
    initial_sidebar_state="collapsed",
)


# ---------------------------------------------------------------------------
# Custom CSS (unchanged)
# ---------------------------------------------------------------------------
CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

.stApp {
    background:
        radial-gradient(circle at 15% 0%, rgba(0, 212, 255, 0.07) 0%, transparent 45%),
        radial-gradient(circle at 85% 100%, rgba(124, 58, 237, 0.07) 0%, transparent 45%),
        #0a0e17;
    color: #e2e8f0;
    font-family: 'Space Grotesk', system-ui, -apple-system, sans-serif;
}

#MainMenu, footer, header { visibility: hidden; height: 0; }
[data-testid="stToolbar"] { display: none; }
[data-testid="stDecoration"] { display: none; }

[data-testid="stAppViewContainer"] > .main .block-container {
    max-width: 760px;
    padding-top: 2.2rem;
    padding-bottom: 1rem;
}

.rusti-header {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 1.2rem 1.5rem;
    margin-bottom: 1.4rem;
    background: linear-gradient(135deg, rgba(17, 24, 39, 0.85), rgba(10, 14, 23, 0.9));
    border: 1px solid rgba(0, 212, 255, 0.12);
    border-radius: 14px;
    box-shadow:
        0 0 40px rgba(0, 212, 255, 0.04),
        inset 0 1px 0 rgba(255, 255, 255, 0.03);
}

.rusti-logo {
    width: 46px;
    height: 46px;
    border-radius: 11px;
    background: linear-gradient(135deg, #00d4ff 0%, #7c3aed 100%);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 24px;
    box-shadow: 0 0 24px rgba(0, 212, 255, 0.35);
    flex-shrink: 0;
}

.rusti-title {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 1.55rem;
    font-weight: 700;
    letter-spacing: 0.14em;
    margin: 0;
    line-height: 1.1;
    background: linear-gradient(90deg, #00d4ff 0%, #a78bfa 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}

.rusti-subtitle {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.68rem;
    color: #64748b;
    letter-spacing: 0.08em;
    margin-top: 4px;
    text-transform: uppercase;
    display: flex;
    align-items: center;
    gap: 6px;
}

.status-dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #00d4ff;
    box-shadow: 0 0 10px #00d4ff;
    animation: pulse 2.2s ease-in-out infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 1;   box-shadow: 0 0 10px #00d4ff; }
    50%      { opacity: 0.4; box-shadow: 0 0 20px #00d4ff; }
}

[data-testid="stChatMessage"] {
    background: rgba(17, 24, 39, 0.55);
    border: 1px solid rgba(148, 163, 184, 0.07);
    border-radius: 12px;
    padding: 0.9rem 1.1rem;
    margin-bottom: 0.7rem;
    backdrop-filter: blur(6px);
    transition: border-color 0.2s ease;
}

[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
    border-left: 2px solid rgba(0, 212, 255, 0.55);
    box-shadow: -3px 0 14px rgba(0, 212, 255, 0.06);
}

[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) {
    border-left: 2px solid rgba(124, 58, 237, 0.55);
    box-shadow: -3px 0 14px rgba(124, 58, 237, 0.06);
}

[data-testid="stChatMessage"] p {
    margin-bottom: 0.45rem;
    line-height: 1.65;
    font-size: 0.95rem;
    color: #e2e8f0;
}

[data-testid="stChatMessage"] p:last-child { margin-bottom: 0; }
[data-testid="stChatMessage"] strong { color: #ffffff; font-weight: 600; }

[data-testid="stChatMessage"] code {
    background: rgba(0, 212, 255, 0.1);
    color: #67e8f9;
    padding: 2px 6px;
    border-radius: 4px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85em;
    border: 1px solid rgba(0, 212, 255, 0.15);
}

[data-testid="stChatMessage"] ul,
[data-testid="stChatMessage"] ol {
    padding-left: 1.3rem;
    margin: 0.4rem 0;
}

[data-testid="stChatMessage"] li { margin-bottom: 0.2rem; }

[data-testid="stChatInput"] {
    background: rgba(17, 24, 39, 0.8);
    border: 1px solid rgba(0, 212, 255, 0.2);
    border-radius: 12px;
    padding: 4px 6px;
    box-shadow: 0 0 20px rgba(0, 212, 255, 0.04);
    transition: border-color 0.25s ease, box-shadow 0.25s ease;
}

[data-testid="stChatInput"]:focus-within {
    border-color: rgba(0, 212, 255, 0.55);
    box-shadow: 0 0 32px rgba(0, 212, 255, 0.18);
}

[data-testid="stChatInput"] textarea {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 0.95rem;
    color: #e2e8f0;
}

[data-testid="stChatInput"] textarea::placeholder {
    color: #475569;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.83rem;
    letter-spacing: 0.02em;
}

.stSpinner > div { border-top-color: #00d4ff !important; }

::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
    background: rgba(0, 212, 255, 0.18);
    border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover { background: rgba(0, 212, 255, 0.35); }

.rusti-empty {
    text-align: center;
    padding: 2.4rem 1rem 1.6rem;
    font-family: 'JetBrains Mono', monospace;
    color: #475569;
    font-size: 0.82rem;
    line-height: 1.9;
    letter-spacing: 0.02em;
}

.rusti-empty .hint {
    color: #00d4ff;
    opacity: 0.55;
    font-size: 0.75rem;
    display: block;
    margin-top: 0.8rem;
}

.stAlert {
    background: rgba(17, 24, 39, 0.9) !important;
    border: 1px solid rgba(239, 68, 68, 0.3) !important;
    border-radius: 10px !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.85rem !important;
}
</style>
"""

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------
if not GROQ_API_KEY:
    st.error(
        "**GROQ_API_KEY is not configured.**\n\n"
        "Streamlit Cloud → your app → **Settings → Secrets** → add:\n\n"
        "```toml\nGROQ_API_KEY = \"gsk_...\"\n```"
    )
    st.stop()

client = AsyncOpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
    timeout=60.0,
)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.markdown(
    """
    <div class="rusti-header">
        <div class="rusti-logo">🦉</div>
        <div>
            <div class="rusti-title">RUSTI</div>
            <div class="rusti-subtitle">
                <span class="status-dot"></span>
                online · risk intelligence
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if not st.session_state.messages:
    st.markdown(
        """
        <div class="rusti-empty">
            ask rusti about solana memecoins, nfts, or crypto concepts<br>
            <span class="hint">
                try · "what's a rug pull?" · "explain liquidity pools" · "is this token safe?"
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Chat — streaming
# ---------------------------------------------------------------------------
if prompt := st.chat_input("Ask RUSTI anything about crypto…"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        async def _stream():
            messages = [{"role": "system", "content": RUSTI_CHAT_PROMPT}]
            for m in st.session_state.messages:
                messages.append({"role": m["role"], "content": m["content"]})

            try:
                stream = await client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=messages,
                    max_tokens=700,
                    temperature=0.5,
                    stream=True,
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield delta
            except Exception as e:
                yield f"\n\n**Error:** `{type(e).__name__}: {e}`"

        answer = st.write_stream(_stream())

    st.session_state.messages.append({"role": "assistant", "content": answer})