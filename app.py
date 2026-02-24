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

# CSS pentru stilizare
st.markdown("""
<style>
    .stChatMessage { font-family: 'Georgia', serif; }
    .stButton button { background-color: #2c3e50; color: white; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. SISTEM DE MEMORIE (SQLite)
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

# Gestionare Sesiune (ID)
if "session_id" not in st.query_params:
    new_id = str(uuid.uuid4())
    st.query_params["session_id"] = new_id
    st.session_state.session_id = new_id
else:
    st.session_state.session_id = st.query_params["session_id"]

# ==========================================
# 3. CONFIGURARE AI & SEARCH
# ==========================================
if "GOOGLE_API_KEY" in st.secrets:
    api_key = st.secrets["GOOGLE_API_KEY"]
else:
    api_key = st.sidebar.text_input("Introdu Google API Key:", type="password")

if not api_key:
    st.warning("Te rog introdu cheia API în sidebar.")
    st.stop()

genai.configure(api_key=api_key)

# Configurare Unealtă Căutare (Sintaxa Nouă)
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

INSTRUCȚIUNI GROUNDING (SEARCH):
1. Verifică activ pe Google modificările recente (ex: Legea 265/2022, modificări fiscale 2026).
2. Dacă utilizatorul întreabă de taxe, caută valorile curente.

REGULI:
- Structură clară: Pași, Acte, Costuri.
- DISCLAIMER OBLIGATORIU: "Sunt un AI, informațiile sunt orientative. Consultați un avocat pentru decizii finale."
"""

try:
    model = genai.GenerativeModel(
        "models/gemini-2.5-flash",
        tools=tools_config,
        system_instruction=PROMPT_AVOCAT
    )
except Exception as e:
    st.error(f"Eroare Model: {e}")
    st.stop()

# ==========================================
# 4. SIDEBAR & FIȘIERE (FIX HTTP ERROR)
# ==========================================
st.sidebar.title("⚖️ Cabinet Virtual")

if st.sidebar.button("🗑️ Caz Nou (Reset)", type="primary"):
    clear_history_db(st.session_state.session_id)
    st.session_state.messages = []
    st.rerun()

enable_audio = st.sidebar.checkbox("🔊 Audio", value=False)
st.sidebar.divider()

st.sidebar.header("📂 Documente")
uploaded_files = st.sidebar.file_uploader("Încarcă acte (PDF/Imagini)", type=["jpg", "png", "jpeg", "pdf"], accept_multiple_files=True)

# Listă pentru fișierele curente (procesate ca bytes)
current_files_data = []

if uploaded_files:
    for up_file in uploaded_files:
        try:
            # Citim fișierul în memorie (Bytes) - EVITĂ HTTP ERROR
            bytes_data = up_file.getvalue()
            mime_type = up_file.type
            
            # Creăm obiectul pentru Gemini
            file_blob = {
                "mime_type": mime_type,
                "data": bytes_data
            }
            current_files_data.append(file_blob)
            
            # Feedback vizual
            if "image" in mime_type:
                st.sidebar.image(up_file, caption=up_file.name, use_container_width=True)
            else:
                st.sidebar.success(f"📄 {up_file.name} pregătit.")
                
        except Exception as e:
            st.sidebar.error(f"Eroare la citirea fișierului {up_file.name}: {e}")

# ==========================================
# 5. ZONA DE CHAT
# ==========================================
st.title("⚖️ Avocat Consultant ONRC")
st.caption("Conectat la Google Search pentru legislație actualizată.")

if "messages" not in st.session_state or not st.session_state.messages:
    st.session_state.messages = load_history_from_db(st.session_state.session_id)

# Afișare mesaje
for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="👤" if msg["role"] == "user" else "⚖️"):
        st.markdown(msg["content"])

# Input
if user_input := st.chat_input("Ex: Vreau să deschid un SRL. Ce acte îmi trebuie?"):
    
    # 1. Salvare User Input
    st.session_state.messages.append({"role": "user", "content": user_input})
    save_message_to_db(st.session_state.session_id, "user", user_input)
    with st.chat_message("user", avatar="👤"):
        st.write(user_input)

    # 2. Pregătire Payload (Text + Fișiere)
    
    # Istoricul text (fără fișiere vechi pentru a nu consuma tokeni)
    history_for_chat = []
    for msg in st.session_state.messages[:-1]:
        role_gemini = "model" if msg["role"] == "assistant" else "user"
        history_for_chat.append({"role": role_gemini, "parts": [msg["content"]]})

    # Mesajul curent
    current_parts = [user_input]
    if current_files_data:
        current_parts.extend(current_files_data) # Adăugăm fișierele direct
        current_parts.append("\n\n(Te rog analizează documentele atașate în contextul întrebării.)")

    # 3. Generare Răspuns
    with st.chat_message("assistant", avatar="⚖️"):
        with st.spinner("Consult legislația și documentele..."):
            try:
                chat = model.start_chat(history=history_for_chat)
                response = chat.send_message(current_parts)
                text_response = response.text
                
                st.markdown(text_response)
                
                # Verificare Grounding
                if response.candidates[0].grounding_metadata.search_entry_point:
                     st.info("🔎 Răspuns verificat prin Google Search.")

                # Salvare
                st.session_state.messages.append({"role": "assistant", "content": text_response})
                save_message_to_db(st.session_state.session_id, "assistant", text_response)

                # Audio
                if enable_audio:
                    clean_text = text_response.replace("*", "").replace("#", "")[:600]
                    sound_file = BytesIO()
                    tts = gTTS(text=clean_text, lang='ro')
                    tts.write_to_fp(sound_file)
                    st.audio(sound_file, format='audio/mp3')

            except Exception as e:
                st.error(f"Eroare: {e}")
