import streamlit as st
from google import genai
from google.genai import types
from PIL import Image
from gtts import gTTS
from io import BytesIO
import sqlite3
import uuid
import time

# ==========================================
# 1. CONFIGURARE PAGINĂ & STIL
# ==========================================
st.set_page_config(
    page_title="Avocat ONRC AI (GenAI v1)",
    page_icon="⚖️",
    layout="wide"
)

st.markdown("""
<style>
    .stChatMessage { font-family: 'Georgia', serif; font-size: 1.05rem; }
    .stButton button { background-color: #2c3e50; color: white; }
    .stSuccess { background-color: #f0fdf4; border: 1px solid #bbf7d0; color: #166534; }
    h1 { color: #1e3a8a; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. CLIENTUL NOU GOOGLE GENAI (CORECTAT PENTRU EROAREA LIST)
# ==========================================

api_key = None

# 1. Extragem din secrete
if "GOOGLE_API_KEY" in st.secrets:
    raw_key = st.secrets["GOOGLE_API_KEY"]
    
    # --- FIX PENTRU EROAREA 'LIST HAS NO ATTRIBUTE STRIP' ---
    # Verificăm dacă cheia a fost citită ca o listă (ex: ["AIza..."])
    if isinstance(raw_key, list):
        api_key = raw_key[0] # Luăm primul element
    else:
        api_key = raw_key    # E deja text

# 2. Dacă nu e în secrete, o cerem manual
if not api_key:
    api_key = st.sidebar.text_input("Introdu Google API Key:", type="password")

# 3. Stop dacă nu avem cheie
if not api_key:
    st.warning("⚠️ Te rog introdu cheia API în sidebar.")
    st.stop()

# 4. Conectare
try:
    # Asigură-te că e string curat
    clean_key = str(api_key).strip()
    client = genai.Client(api_key=clean_key)
except Exception as e:
    st.error(f"Eroare la conectarea cu Google AI: {e}")
    st.stop()

# ==========================================
# 3. MEMORIE SQLITE
# ==========================================
def init_db():
    conn = sqlite3.connect('legal_chat_v3.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history 
                 (session_id TEXT, role TEXT, content TEXT, timestamp REAL)''')
    conn.commit()
    conn.close()

def save_message(session_id, role, content):
    conn = sqlite3.connect('legal_chat_v3.db')
    c = conn.cursor()
    c.execute("INSERT INTO history VALUES (?, ?, ?, ?)", (session_id, role, content, time.time()))
    conn.commit()
    conn.close()

def load_history(session_id):
    conn = sqlite3.connect('legal_chat_v3.db')
    c = conn.cursor()
    c.execute("SELECT role, content FROM history WHERE session_id=? ORDER BY timestamp ASC", (session_id,))
    data = c.fetchall()
    conn.close()
    return [{"role": row[0], "content": row[1]} for row in data]

def clear_history(session_id):
    conn = sqlite3.connect('legal_chat_v3.db')
    c = conn.cursor()
    c.execute("DELETE FROM history WHERE session_id=?", (session_id,))
    conn.commit()
    conn.close()

init_db()

# ID Sesiune
if "session_id" not in st.query_params:
    st.session_state.session_id = str(uuid.uuid4())
    st.query_params["session_id"] = st.session_state.session_id
else:
    st.session_state.session_id = st.query_params["session_id"]

# ==========================================
# 4. CONFIGURARE AVOCAT
# ==========================================
PROMPT_AVOCAT = """
Ești un Avocat Virtual Expert în ONRC și Legislație Comercială (România).

OBIECTIV:
Oferi consultanță juridică preliminară clară.

REGULI CRITICE:
1. GROUNDING: Folosește Google Search pentru a verifica legile din 2024-2026 (taxe, proceduri noi, Legea 265/2022).
2. DOSAR: Dacă există documente atașate, analizează-le cu prioritate.
3. TON: Profesional, dar explicativ. 
4. DISCLAIMER: La final, menționează scurt că ești un AI și info nu e consultanță oficială.
"""

# Configurare Unelte (Noua Sintaxă SDK v1)
search_tool = types.Tool(
    google_search=types.GoogleSearch()
)

generate_config = types.GenerateContentConfig(
    system_instruction=PROMPT_AVOCAT,
    tools=[search_tool],
    temperature=0.3
)

# ==========================================
# 5. SIDEBAR - DOSAR (MEMORIE FIȘIERE)
# ==========================================
st.sidebar.title("🗂️ Dosar Acte")

# Reset
if st.sidebar.button("🗑️ Resetare Caz", type="primary"):
    clear_history(st.session_state.session_id)
    st.session_state.messages = []
    st.session_state.file_bytes_store = [] 
    st.rerun()

st.sidebar.divider()

# Stocare date brute fișiere în sesiune
if "file_bytes_store" not in st.session_state:
    st.session_state.file_bytes_store = [] 

uploaded_files = st.sidebar.file_uploader("Adaugă la Dosar", type=["jpg", "png", "pdf"], accept_multiple_files=True)

if uploaded_files:
    if st.sidebar.button("📥 Procesează Documente"):
        for up_file in uploaded_files:
            # Verificăm duplicarea
            if not any(f['name'] == up_file.name for f in st.session_state.file_bytes_store):
                try:
                    file_data = {
                        "name": up_file.name,
                        "mime": up_file.type,
                        "data": up_file.getvalue()
                    }
                    st.session_state.file_bytes_store.append(file_data)
                    st.sidebar.success(f"✅ {up_file.name} adăugat.")
                except Exception as e:
                    st.sidebar.error(f"Eroare: {e}")
            else:
                st.sidebar.warning(f"⚠️ {up_file.name} există deja.")
        
        time.sleep(1) # Refresh UI
        st.rerun()

# Afișare stare dosar
if st.session_state.file_bytes_store:
    st.sidebar.info(f"Dosar activ: {len(st.session_state.file_bytes_store)} documente")
    for f in st.session_state.file_bytes_store:
        st.sidebar.text(f"📄 {f['name']}")
else:
    st.sidebar.caption("Dosar gol.")

enable_audio = st.sidebar.checkbox("🔊 Audio", value=False)

# ==========================================
# 6. CHAT INTERFACE
# ==========================================
st.title("⚖️ Avocat Consultant ONRC")
st.caption("Expertiză juridică asistată de AI • Conectat la Google Search")

if "messages" not in st.session_state or not st.session_state.messages:
    st.session_state.messages = load_history(st.session_state.session_id)

# Afișare Chat
for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="👤" if msg["role"] == "user" else "⚖️"):
        st.markdown(msg["content"])

# INPUT UTILIZATOR
if user_input := st.chat_input("Întreabă avocatul..."):
    
    # 1. Salvare Input
    st.session_state.messages.append({"role": "user", "content": user_input})
    save_message(st.session_state.session_id, "user", user_input)
    with st.chat_message("user", avatar="👤"):
        st.write(user_input)

    # 2. Construire Payload (Istoric + Fișiere + Întrebare)
    contents_payload = []
    
    # Adăugăm istoricul text anterior
    for msg in st.session_state.messages[:-1]:
        role_gemini = "model" if msg["role"] == "assistant" else "user"
        contents_payload.append(types.Content(
            role=role_gemini,
            parts=[types.Part.from_text(text=msg["content"])]
        ))
    
    # Construim mesajul CURENT
    current_message_parts = []
    
    # Adăugăm fișierele din dosar
    if st.session_state.file_bytes_store:
        for f_store in st.session_state.file_bytes_store:
            part = types.Part.from_bytes(
                data=f_store['data'],
                mime_type=f_store['mime']
            )
            current_message_parts.append(part)
        
        current_message_parts.append(types.Part.from_text(text="\n\n[SISTEM: Analizează documentele de mai sus din dosarul clientului.]"))
    
    # Adăugăm întrebarea text
    current_message_parts.append(types.Part.from_text(text=user_input))
    
    # Adăugăm totul la payload
    contents_payload.append(types.Content(
        role="user",
        parts=current_message_parts
    ))

    # 3. Generare Răspuns (Streaming)
    with st.chat_message("assistant", avatar="⚖️"):
        placeholder = st.empty()
        full_text = ""
        
        try:
            # APELUL CĂTRE NOUL SDK
            response_stream = client.models.generate_content_stream(
                model='gemini-2.5-flash-lite',
                contents=contents_payload,
                config=generate_config
            )
            
            for chunk in response_stream:
                if chunk.text:
                    full_text += chunk.text
                    placeholder.markdown(full_text + "▌")
            
            placeholder.markdown(full_text)
            
            # Salvare
            st.session_state.messages.append({"role": "assistant", "content": full_text})
            save_message(st.session_state.session_id, "assistant", full_text)

            # Audio
            if enable_audio:
                clean_text = full_text.replace("*", "")[:500]
                sound = BytesIO()
                tts = gTTS(text=clean_text, lang='ro')
                tts.write_to_fp(sound)
                st.audio(sound, format='audio/mp3')

        except Exception as e:
            st.error(f"Eroare comunicare AI: {e}")
