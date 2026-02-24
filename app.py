import streamlit as st
from google import genai
from google.genai import types
from PIL import Image
from gtts import gTTS
from io import BytesIO
import sqlite3
import uuid
import time
import base64

# ==========================================
# 1. CONFIGURARE PAGINĂ & STIL
# ==========================================
st.set_page_config(page_title="Avocat ONRC AI (GenAI v1)", page_icon="⚖️", layout="wide")

st.markdown("""
<style>
    .stChatMessage { font-family: 'Georgia', serif; font-size: 1.05rem; }
    .stButton button { background-color: #2c3e50; color: white; }
    .stSuccess { background-color: #f0fdf4; border: 1px solid #bbf7d0; color: #166534; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. CLIENTUL NOU GOOGLE GENAI
# ==========================================
if "GOOGLE_API_KEY" in st.secrets:
    api_key = st.secrets["GOOGLE_API_KEY"]
else:
    api_key = st.sidebar.text_input("Introdu Google API Key:", type="password")

if not api_key:
    st.warning("⚠️ Te rog introdu cheia API.")
    st.stop()

# Initializare Client Nou
client = genai.Client(api_key=api_key)

# ==========================================
# 3. MEMORIE SQLITE (Păstrată)
# ==========================================
def init_db():
    conn = sqlite3.connect('legal_chat_v2.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history 
                 (session_id TEXT, role TEXT, content TEXT, timestamp REAL)''')
    conn.commit()
    conn.close()

def save_message(session_id, role, content):
    conn = sqlite3.connect('legal_chat_v2.db')
    c = conn.cursor()
    c.execute("INSERT INTO history VALUES (?, ?, ?, ?)", (session_id, role, content, time.time()))
    conn.commit()
    conn.close()

def load_history(session_id):
    conn = sqlite3.connect('legal_chat_v2.db')
    c = conn.cursor()
    c.execute("SELECT role, content FROM history WHERE session_id=? ORDER BY timestamp ASC", (session_id,))
    data = c.fetchall()
    conn.close()
    return [{"role": row[0], "content": row[1]} for row in data]

def clear_history(session_id):
    conn = sqlite3.connect('legal_chat_v2.db')
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
# 4. CONFIGURARE AVOCAT (SYSTEM PROMPT & TOOLS)
# ==========================================
PROMPT_AVOCAT = """
Ești un Avocat Virtual Expert în ONRC și Legislație Comercială (România).

OBIECTIV:
Oferi consultanță juridică preliminară clară.

REGULI CRITICE:
1. GROUNDING: Folosește Google Search pentru a verifica legile din 2024-2025 (taxe, proceduri noi, Legea 265/2022).
2. DOSAR: Dacă există documente atașate, analizează-le cu prioritate.
3. TON: Profesional, dar explicativ. Nu folosi "avocăreza" fără a explica termenii.
4. DISCLAIMER: La final, menționează scurt că ești un AI și info nu e consultanță oficială.
"""

# Configurare Unelte (Noua Sintaxă)
search_tool = types.Tool(
    google_search=types.GoogleSearch()
)

# Configurare Generare
generate_config = types.GenerateContentConfig(
    system_instruction=PROMPT_AVOCAT,
    tools=[search_tool],
    temperature=0.3 # Mai precis pentru legal
)

# ==========================================
# 5. SIDEBAR - DOSAR (MEMORIE FIȘIERE)
# ==========================================
st.sidebar.title("🗂️ Dosar Acte")

# Reset
if st.sidebar.button("🗑️ Resetare Caz", type="primary"):
    clear_history(st.session_state.session_id)
    st.session_state.messages = []
    st.session_state.file_parts = [] # Resetăm fișierele din memorie
    st.rerun()

st.sidebar.divider()

# Stocare fișiere în sesiune (ca obiecte 'types.Part' gata de trimis)
if "file_parts" not in st.session_state:
    st.session_state.file_parts = []

uploaded_files = st.sidebar.file_uploader("Adaugă la Dosar", type=["jpg", "png", "pdf"], accept_multiple_files=True)

if uploaded_files:
    if st.sidebar.button("📥 Procesează Documente"):
        for up_file in uploaded_files:
            try:
                # 1. Citim fișierul ca bytes
                file_bytes = up_file.getvalue()
                mime_type = up_file.type
                
                # 2. Creăm obiectul Part (Noua metodă SDK)
                # Acesta este formatul nativ pe care îl așteaptă noul client
                file_part = types.Part.from_bytes(
                    data=file_bytes,
                    mime_type=mime_type
                )
                
                st.session_state.file_parts.append(file_part)
                st.sidebar.success(f"✅ {up_file.name} memorat.")
                
            except Exception as e:
                st.sidebar.error(f"Eroare procesare {up_file.name}: {e}")
        time.sleep(1)
        st.rerun()

# Afișare stare dosar
if st.session_state.file_parts:
    st.sidebar.info(f"Dosar activ: {len(st.session_state.file_parts)} documente")
else:
    st.sidebar.caption("Dosar gol.")

enable_audio = st.sidebar.checkbox("🔊 Audio", value=False)

# ==========================================
# 6. CHAT INTERFACE
# ==========================================
st.title("⚖️ Avocat Consultant ONRC")

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

    # 2. Construire Istoric + Fișiere (NOUA LOGICĂ)
    contents_payload = []
    
    # Adăugăm istoricul text anterior (pentru context)
    for msg in st.session_state.messages[:-1]:
        role_gemini = "model" if msg["role"] == "assistant" else "user"
        contents_payload.append(types.Content(
            role=role_gemini,
            parts=[types.Part.from_text(text=msg["content"])]
        ))
    
    # Construim mesajul CURENT
    current_message_parts = []
    
    # A. Adăugăm fișierele din dosar (dacă există)
    if st.session_state.file_parts:
        current_message_parts.extend(st.session_state.file_parts)
        current_message_parts.append(types.Part.from_text(text="\n\n[SISTEM: Analizează documentele de mai sus din dosarul clientului.]"))
    
    # B. Adăugăm întrebarea text
    current_message_parts.append(types.Part.from_text(text=user_input))
    
    # Adăugăm mesajul curent la payload
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
            # generate_content_stream este noua metodă
            response_stream = client.models.generate_content_stream(
                model='gemini-2.5-flash',
                contents=contents_payload,
                config=generate_config
            )
            
            for chunk in response_stream:
                if chunk.text:
                    full_text += chunk.text
                    placeholder.markdown(full_text + "▌")
            
            placeholder.markdown(full_text)
            
            # Verificare Grounding (Google Search) - Structura e diferită în noul SDK
            # De obicei info despre grounding vine în ultimul chunk sau în response metadata
            # Simplificare: afișăm doar textul, noul SDK integrează sursele în text adesea.

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
            # Debugging pentru noul SDK
            # st.error(str(e))
