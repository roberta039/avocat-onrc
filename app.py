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
    .stSuccess { background-color: #d4edda; color: #155724; padding: 10px; border-radius: 5px;}
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

# Gestionare ID Sesiune
if "session_id" not in st.query_params:
    new_id = str(uuid.uuid4())
    st.query_params["session_id"] = new_id
    st.session_state.session_id = new_id
else:
    st.session_state.session_id = st.query_params["session_id"]

# ==========================================
# 3. CONFIGURARE AI
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
Oferi consultanță preliminară clară, bazată pe legislația la zi (2024-2025).

INSTRUCȚIUNI:
1. Verifică activ pe Google modificările recente.
2. Analizează TOATE documentele din dosarul curent dacă este cazul.
3. Fii concis și profesionist.

DISCLAIMER: "Info orientativă. Nu ține loc de avocat."
"""

model = genai.GenerativeModel(
    "models/gemini-1.5-flash",
    tools=tools_config,
    system_instruction=PROMPT_AVOCAT
)

# ==========================================
# 4. SIDEBAR - GESTIONARE DOSAR (FIȘIERE MULTIPLE)
# ==========================================
st.sidebar.title("⚖️ Cabinet Virtual")

# Inițializare listă fișiere în sesiune
if "dosar_files" not in st.session_state:
    st.session_state.dosar_files = []

# Buton Reset Total
if st.sidebar.button("🗑️ Resetare Caz (Tot)", type="primary"):
    clear_history_db(st.session_state.session_id)
    st.session_state.messages = []
    st.session_state.dosar_files = [] # Ștergem și fișierele
    st.rerun()

st.sidebar.divider()

# --- ZONA UPLOAD ---
st.sidebar.header("📂 Adaugă la Dosar")
uploaded_files_widget = st.sidebar.file_uploader("Selectează documente", type=["jpg", "png", "pdf"], accept_multiple_files=True, key="uploader")

if uploaded_files_widget:
    if st.sidebar.button("📥 Salvează în Dosar"):
        for up_file in uploaded_files_widget:
            # Verificăm să nu existe deja (după nume)
            if not any(f['name'] == up_file.name for f in st.session_state.dosar_files):
                try:
                    file_data = {
                        "name": up_file.name,
                        "mime_type": up_file.type,
                        "data": up_file.getvalue() # Citim bytes direct
                    }
                    st.session_state.dosar_files.append(file_data)
                    st.sidebar.success(f"✅ {up_file.name} adăugat!")
                except Exception as e:
                    st.sidebar.error(f"Eroare: {e}")
            else:
                st.sidebar.warning(f"⚠️ {up_file.name} este deja în dosar.")
        
        # Mic truc pentru a face refresh la interfață
        time.sleep(0.5)
        st.rerun()

# --- ZONA AFIȘARE DOSAR ---
st.sidebar.subheader(f"Dosar Curent ({len(st.session_state.dosar_files)} acte)")

if st.session_state.dosar_files:
    # Afișăm lista de fișiere memorate
    for file_info in st.session_state.dosar_files:
        st.sidebar.text(f"📄 {file_info['name']}")
    
    # Buton golire doar fișiere
    if st.sidebar.button("❌ Golește doar Dosarul"):
        st.session_state.dosar_files = []
        st.rerun()
else:
    st.sidebar.caption("Niciun document în memorie.")

enable_audio = st.sidebar.checkbox("🔊 Audio", value=False)

# ==========================================
# 5. CHAT LOGIC
# ==========================================
st.title("⚖️ Avocat Consultant ONRC")
st.caption("Documentele adăugate în dosar rămân în memorie pe parcursul conversației.")

# Încărcare mesaje
if "messages" not in st.session_state or not st.session_state.messages:
    st.session_state.messages = load_history_from_db(st.session_state.session_id)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="👤" if msg["role"] == "user" else "⚖️"):
        st.markdown(msg["content"])

# INPUT
if user_input := st.chat_input("Întreabă despre documentele din dosar..."):
    
    st.session_state.messages.append({"role": "user", "content": user_input})
    save_message_to_db(st.session_state.session_id, "user", user_input)
    with st.chat_message("user", avatar="👤"):
        st.write(user_input)

    # PREGĂTIRE CONTEXT
    history_for_chat = []
    for msg in st.session_state.messages[:-1]:
        role_gemini = "model" if msg["role"] == "assistant" else "user"
        history_for_chat.append({"role": role_gemini, "parts": [msg["content"]]})

    # Construim mesajul curent: Text + TOATE fișierele din dosar
    current_parts = [user_input]
    
    if st.session_state.dosar_files:
        current_parts.append("\nCONTEXT: Următoarele documente sunt în dosarul clientului. Folosește-le pentru a răspunde:")
        for f in st.session_state.dosar_files:
            # Reconstruim obiectul pentru Gemini din memoria sesiunii
            current_parts.append({
                "mime_type": f["mime_type"],
                "data": f["data"]
            })

    # GENERARE
    with st.chat_message("assistant", avatar="⚖️"):
        message_placeholder = st.empty()
        full_response = ""
        
        try:
            chat = model.start_chat(history=history_for_chat)
            
            # Streaming pentru viteză
            response_stream = chat.send_message(current_parts, stream=True)
            
            for chunk in response_stream:
                if chunk.text:
                    full_response += chunk.text
                    message_placeholder.markdown(full_response + "▌")
            
            message_placeholder.markdown(full_response)
            
            # Verificare Grounding
            try:
                if response_stream.resolve().candidates[0].grounding_metadata.search_entry_point:
                    st.info("🔎 Verificat pe Google.")
            except:
                pass

            # Salvare
            st.session_state.messages.append({"role": "assistant", "content": full_response})
            save_message_to_db(st.session_state.session_id, "assistant", full_response)

            # Audio
            if enable_audio:
                clean_text = full_response.replace("*", "")[:600]
                sound_file = BytesIO()
                tts = gTTS(text=clean_text, lang='ro')
                tts.write_to_fp(sound_file)
                st.audio(sound_file, format='audio/mp3')

        except Exception as e:
            st.error(f"Eroare: {e}")
