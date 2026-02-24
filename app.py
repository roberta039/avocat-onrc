import streamlit as st
import google.generativeai as genai
from PIL import Image
import tempfile
from gtts import gTTS
from io import BytesIO
import sqlite3
import uuid
import time
import os

# ==========================================
# 1. CONFIGURARE PAGINĂ & STIL
# ==========================================
st.set_page_config(
    page_title="Avocat AI - Expert ONRC",
    page_icon="⚖️",
    layout="wide"
)

# CSS pentru un aspect profesional
st.markdown("""
<style>
    .stChatMessage { font-family: 'Georgia', serif; }
    h1 { color: #2c3e50; }
    .stButton button { background-color: #2c3e50; color: white; }
    .reportview-container .main .block-container{ max-width: 1000px; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. SISTEM DE MEMORIE (SQLITE)
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

# Gestionare ID Sesiune (pentru persistență la refresh)
if "session_id" not in st.query_params:
    new_id = str(uuid.uuid4())
    st.query_params["session_id"] = new_id
    st.session_state.session_id = new_id
else:
    st.session_state.session_id = st.query_params["session_id"]

# ==========================================
# 3. CONFIGURARE AI & SEARCH TOOL
# ==========================================

# API Key
if "GOOGLE_API_KEY" in st.secrets:
    api_key = st.secrets["GOOGLE_API_KEY"]
else:
    api_key = st.sidebar.text_input("Introdu Google API Key:", type="password")

if not api_key:
    st.warning("🔒 Te rog introdu cheia API în sidebar pentru a activa avocatul.")
    st.stop()

genai.configure(api_key=api_key)

# Configurare Unealtă Căutare (Grounding)
tools_config = [
    {"google_search": {}} # Activează căutarea nativă
]

# Prompt Avocat ONRC
PROMPT_AVOCAT = """
Ești un Avocat Virtual Senior, expert în Drept Comercial, Proceduri ONRC și Fiscalitate (România).

OBIECTIV:
Oferi consultanță juridică preliminară clară antreprenorilor.

INSTRUCȚIUNI SPECIALE (SEARCH GROUNDING):
1. Folosește Google Search activ pentru a verifica orice modificare legislativă recentă (2024-2026).
2. Verifică taxele ONRC actuale și procedurile din Legea 265/2022 (digitalizare).
3. Dacă utilizatorul întreabă de o lege viitoare, caută "proiecte legislative" sau "propuneri modificare cod fiscal".

REGULI DE RĂSPUNS:
- Fii precis: Citează articolul de lege când e posibil.
- Structură: Pas 1, Pas 2, Acte Necesare, Costuri Estimative.
- Avertisment: Include mereu disclaimer-ul că ești un AI.

LIMITĂRI:
- Nu poți reprezenta clientul în instanță.
- Nu poți semna acte în locul lui.
"""

try:
    model = genai.GenerativeModel(
        "models/gemini-2.5-flash", 
        tools=tools_config,
        system_instruction=PROMPT_AVOCAT
    )
except Exception as e:
    st.error(f"Eroare configurare model: {e}")
    st.stop()

# ==========================================
# 4. INTERFAȚA UTILIZATOR (SIDEBAR)
# ==========================================
st.sidebar.title("⚖️ Cabinet Avocat AI")
st.sidebar.info("Specializare: Înființări Firme, Mențiuni ONRC, Litigii Comerciale.")

# Buton Reset
if st.sidebar.button("🗑️ Șterge Discuția (Caz Nou)", type="primary"):
    clear_history_db(st.session_state.session_id)
    st.session_state.messages = []
    st.rerun()

enable_audio = st.sidebar.checkbox("🔊 Activează Răspuns Audio", value=False)
st.sidebar.divider()

# Upload Documente
st.sidebar.header("📂 Analiză Acte")
uploaded_files = st.sidebar.file_uploader("Încarcă C.I., Act Constitutiv (PDF/Poză)", type=["jpg", "png", "pdf"], accept_multiple_files=True)

current_context_files = []
if uploaded_files:
    for up_file in uploaded_files:
        if "image" in up_file.type:
            img = Image.open(up_file)
            current_context_files.append(img)
            st.sidebar.image(img, caption="Document scanat", use_container_width=True)
        elif "pdf" in up_file.type:
            # Procesare PDF (Upload temporar la Google)
            with st.spinner(f"Analizez PDF: {up_file.name}..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(up_file.getvalue())
                    path = tmp.name
                
                # Încărcare cu caching simplu (session state)
                key = f"pdf_{up_file.name}"
                if key not in st.session_state:
                    file_ref = genai.upload_file(path, mime_type="application/pdf")
                    st.session_state[key] = file_ref
                
                current_context_files.append(st.session_state[key])
                st.sidebar.success(f"📄 {up_file.name} atașat la dosar.")

# ==========================================
# 5. ZONA DE CHAT
# ==========================================

st.title("⚖️ Avocat Consultant - Registrul Comerțului")
st.caption("Verific legislația la zi folosind Google Search. • *Disclaimer: Informare, nu consultanță juridică oficială.*")

# Încărcare istoric
if "messages" not in st.session_state or not st.session_state.messages:
    st.session_state.messages = load_history_from_db(st.session_state.session_id)

# Afișare istoric
for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="👤" if msg["role"] == "user" else "⚖️"):
        st.markdown(msg["content"])

# Input Utilizator
if user_input := st.chat_input("Ex: Vreau să deschid un PFA. Ce acte îmi trebuie?"):
    
    # 1. Afișare User
    st.session_state.messages.append({"role": "user", "content": user_input})
    save_message_to_db(st.session_state.session_id, "user", user_input)
    with st.chat_message("user", avatar="👤"):
        st.write(user_input)

    # 2. Pregătire Context pentru AI
    # Construim istoricul pentru chat session (doar text pentru a economisi tokeni/erori)
    history_obj = []
    for msg in st.session_state.messages[:-1]:
        role_gemini = "model" if msg["role"] == "assistant" else "user"
        history_obj.append({"role": role_gemini, "parts": [msg["content"]]})

    # Mesajul curent include textul + fișierele atașate ACUM
    current_message_parts = [user_input]
    if current_context_files:
        current_message_parts.extend(current_context_files)
        current_message_parts.append("\n\n(Analizează documentele atașate în contextul întrebării mele.)")

    # 3. Generare Răspuns
    with st.chat_message("assistant", avatar="⚖️"):
        with st.spinner("Consult Monitorul Oficial și baza de date..."):
            try:
                # Inițializare chat cu istoric
                chat = model.start_chat(history=history_obj)
                
                # Trimitere mesaj (activează automat Google Search dacă e nevoie)
                response = chat.send_message(current_message_parts)
                text_response = response.text
                
                st.markdown(text_response)

                # Afișare surse (dacă a folosit Google Search)
                if response.candidates[0].grounding_metadata.search_entry_point:
                     st.info("🔎 Răspuns verificat prin Google Search.")

                # Salvare în DB
                st.session_state.messages.append({"role": "assistant", "content": text_response})
                save_message_to_db(st.session_state.session_id, "assistant", text_response)

                # Generare Audio
                if enable_audio:
                    # Curățare text pentru audio (fără linkuri lungi sau caractere speciale)
                    clean_text = text_response.replace("*", "").replace("#", "")[:600] # Limita 600 caractere pt viteză
                    sound_file = BytesIO()
                    tts = gTTS(text=clean_text, lang='ro')
                    tts.write_to_fp(sound_file)
                    st.audio(sound_file, format='audio/mp3')

            except Exception as e:
                st.error(f"Eroare de sistem: {e}")
                if "safety" in str(e).lower():
                    st.warning("Mesajul a fost blocat de filtrele de siguranță.")
