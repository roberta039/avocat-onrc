import streamlit as st
import google.generativeai as genai
from PIL import Image
from gtts import gTTS
from io import BytesIO
import sqlite3
import uuid
import time

# ==========================================
# 1. CONFIGURARE PAGINĂ
# ==========================================
st.set_page_config(
    page_title="Avocat ONRC AI",
    page_icon="⚖️",
    layout="wide"
)

st.markdown("""
<style>
    .stChatMessage { font-family: 'Georgia', serif; font-size: 1.05rem; }
    .stButton button { background-color: #2c3e50; color: white; }
    /* Ascundem cursorul de streaming la final */
    .stMarkdown p { margin-bottom: 0.5rem; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. MEMORIE (SQLite)
# ==========================================
def init_db():
    conn = sqlite3.connect('legal_chat.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history 
                 (session_id TEXT, role TEXT, content TEXT, timestamp REAL)''')
    conn.commit()
    conn.close()

def save_message_to_db(session_id, role, content):
    conn = sqlite3.connect('legal_chat.db')
    c = conn.cursor()
    c.execute("INSERT INTO history VALUES (?, ?, ?, ?)", (session_id, role, content, time.time()))
    conn.commit()
    conn.close()

def load_history_from_db(session_id):
    conn = sqlite3.connect('legal_chat.db')
    c = conn.cursor()
    c.execute("SELECT role, content FROM history WHERE session_id=? ORDER BY timestamp ASC", (session_id,))
    data = c.fetchall()
    conn.close()
    return [{"role": row[0], "content": row[1]} for row in data]

def clear_history_db(session_id):
    conn = sqlite3.connect('legal_chat.db')
    c = conn.cursor()
    c.execute("DELETE FROM history WHERE session_id=?", (session_id,))
    conn.commit()
    conn.close()

init_db()

if "session_id" not in st.query_params:
    new_id = str(uuid.uuid4())
    st.query_params["session_id"] = new_id
    st.session_state.session_id = new_id
else:
    st.session_state.session_id = st.query_params["session_id"]

# ==========================================
# 3. CONFIGURARE AI (CU RETRY)
# ==========================================
if "GOOGLE_API_KEY" in st.secrets:
    api_key = st.secrets["GOOGLE_API_KEY"]
else:
    api_key = st.sidebar.text_input("Introdu Google API Key:", type="password")

if not api_key:
    st.warning("Te rog introdu cheia API.")
    st.stop()

genai.configure(api_key=api_key)

# Configurare Tools (Search)
tools_config = [
    {
        "google_search_retrieval": {
            "dynamic_retrieval_config": {
                "mode": "dynamic",
                "dynamic_threshold": 0.6,
            }
        }
    }
]

PROMPT_AVOCAT = """
Ești un Avocat Virtual Senior, expert în Drept Comercial și Proceduri ONRC (România).

OBIECTIV:
Oferi consultanță preliminară clară, bazată pe legislația la zi (2023-2026).

INSTRUCȚIUNI:
1. Verifică activ pe Google modificările recente (ex: Legea 265/2022).
2. Dacă fișierul atașat este mare, fă un rezumat juridic.
3. Dacă primești erori de conectare, fii concis.

DISCLAIMER: "Info orientativă. Nu ține loc de avocat."
"""

model = genai.GenerativeModel(
    "models/gemini-2.5-flash",
    tools=tools_config,
    system_instruction=PROMPT_AVOCAT
)

# ==========================================
# 4. SIDEBAR & FILE HANDLER
# ==========================================
st.sidebar.title("⚖️ Cabinet Virtual")

if st.sidebar.button("🗑️ Resetare Caz", type="primary"):
    clear_history_db(st.session_state.session_id)
    st.session_state.messages = []
    st.rerun()

enable_audio = st.sidebar.checkbox("🔊 Audio (La final)", value=False)
st.sidebar.divider()

uploaded_files = st.sidebar.file_uploader("Încarcă acte", type=["jpg", "png", "pdf"], accept_multiple_files=True)

current_files_data = []
if uploaded_files:
    for up_file in uploaded_files:
        try:
            # Citim direct în memorie (fără upload HTTP)
            bytes_data = up_file.getvalue()
            current_files_data.append({
                "mime_type": up_file.type,
                "data": bytes_data
            })
            if "image" in up_file.type:
                st.sidebar.image(up_file, caption="Atașament", use_container_width=True)
            else:
                st.sidebar.success(f"📄 {up_file.name}")
        except Exception as e:
            st.sidebar.error(f"Eroare fișier: {e}")

# ==========================================
# 5. CHAT LOGIC (STREAMING IMPLEMENTAT)
# ==========================================
st.title("⚖️ Avocat Consultant ONRC")
st.caption("Sistem conectat la Monitorul Oficial via Google Search.")

# Încărcare mesaje
if "messages" not in st.session_state or not st.session_state.messages:
    st.session_state.messages = load_history_from_db(st.session_state.session_id)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="👤" if msg["role"] == "user" else "⚖️"):
        st.markdown(msg["content"])

# INPUT
if user_input := st.chat_input("Ex: Ce taxe am pentru un SRL în 2025?"):
    
    # Afișare User
    st.session_state.messages.append({"role": "user", "content": user_input})
    save_message_to_db(st.session_state.session_id, "user", user_input)
    with st.chat_message("user", avatar="👤"):
        st.write(user_input)

    # Pregătire Date
    history_for_chat = []
    for msg in st.session_state.messages[:-1]:
        role_gemini = "model" if msg["role"] == "assistant" else "user"
        history_for_chat.append({"role": role_gemini, "parts": [msg["content"]]})

    current_parts = [user_input]
    if current_files_data:
        current_parts.extend(current_files_data)
        current_parts.append("\n(Analizează documentele atașate)")

    # GENERARE CU STREAMING (Rezolvă eroarea 504)
    with st.chat_message("assistant", avatar="⚖️"):
        message_placeholder = st.empty()
        full_response = ""
        
        try:
            chat = model.start_chat(history=history_for_chat)
            
            # --- AICI E CHEIA: stream=True ---
            response_stream = chat.send_message(current_parts, stream=True)
            
            # Iterăm prin bucățile de text pe măsură ce vin
            for chunk in response_stream:
                if chunk.text:
                    full_response += chunk.text
                    # Facem update vizual la fiecare cuvânt
                    message_placeholder.markdown(full_response + "▌")
            
            # Afișare finală curată
            message_placeholder.markdown(full_response)
            
            # Verificare Grounding (dacă e disponibil în ultimul chunk)
            try:
                if response_stream.resolve().candidates[0].grounding_metadata.search_entry_point:
                    st.info("🔎 Verificat pe Google.")
            except:
                pass

            # Salvare DB
            st.session_state.messages.append({"role": "assistant", "content": full_response})
            save_message_to_db(st.session_state.session_id, "assistant", full_response)

            # Audio (Doar după ce s-a terminat tot textul)
            if enable_audio:
                clean_text = full_response.replace("*", "")[:600]
                sound_file = BytesIO()
                tts = gTTS(text=clean_text, lang='ro')
                tts.write_to_fp(sound_file)
                st.audio(sound_file, format='audio/mp3')

        except Exception as e:
            st.error(f"Eroare conexiune: {e}")
            if "504" in str(e):
                st.warning("⚠️ Răspunsul a durat prea mult. Încearcă să încarci un PDF mai mic sau să pui o întrebare mai scurtă.")
