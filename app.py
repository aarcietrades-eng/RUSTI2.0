import streamlit as st
from analysis import analyze_token, client
from config import GROQ_MODEL
from prompts import RUSTI_CHAT_PROMPT
import asyncio

st.set_page_config(page_title="RUSTI", page_icon="🦉", layout="wide")
st.title("🦉 RUSTI — Risk Understanding & Speculative Token Intelligence")

# Initialize chat history in the session
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display past messages
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat input
if prompt := st.chat_input("Ask about a token, or paste a contract address…"):
    # Add user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Build the request to the model (reusing your existing client)
    with st.chat_message("assistant"):
        with st.spinner("RUSTI is thinking…"):
            try:
                # Simple version: just chat. For /analyze, you can add button triggers.
                resp = asyncio.run(client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[
                        {"role": "system", "content": RUSTI_CHAT_PROMPT},
                        *[{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]
                    ],
                    max_tokens=700,
                    temperature=0.5,
                ))
                answer = resp.choices[0].message.content
            except Exception as e:
                answer = f"**Error:** `{e}`"

            st.markdown(answer)
            st.session_state.messages.append({"role": "assistant", "content": answer})