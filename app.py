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
# 1. CONFIGURARE PAGINĂ
# ==========================================
st.set_page_config(page_title="Avocat ONRC AI", page_icon="⚖️", layout="wide")

st.markdown("""
<style>
    .stChatMessage { font-family: 'Georgia', serif; font-size: 1.05rem; }
    .stButton button { background-color: #2c3e50; color: white; }
    .stSuccess { background-color: #f0fdf4; border: 1px solid #bbf7d0; color: #166534; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. CLIENTUL GOOGLE GENAI (V1.0)
# ==========================================
api_key = None
if "GOOGLE_API_KEY" in st.secrets:
    raw_key = st.secrets["GOOGLE_API_KEY"]
    if isinstance(raw_key, list):
        api_key = raw_key[0]
    else:
        api_key = raw_key

if not api_key:
    api_key = st.sidebar.text_input("Introdu Google API Key:", type="password")

if not api_key:
    st.warning("⚠️ Te rog introdu cheia API.")
    st.stop()

try:
    # Clientul nou
    client = genai.Client(api_key=str(api_key).strip())
except Exception as e:
    st.error(f"Eroare conectare: {e}")
    st.stop()

# ==========================================
# 3. MEMORIE SQLITE
# ==========================================
def init_db():
    conn = sqlite3.connect('legal_chat_v4.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history 
                 (session_id TEXT, role TEXT, content TEXT, timestamp REAL)''')
    conn.commit()
    conn.close()

def save_message(session_id, role, content):
    conn = sqlite3.connect('legal_chat_v4.db')
    c = conn.cursor()
    c.execute("INSERT INTO history VALUES (?, ?, ?, ?)", (session_id, role, content, time.time()))
    conn.commit()
    conn.close()

def load_history(session_id):
    conn = sqlite3.connect('legal_chat_v4.db')
    c = conn.cursor()
    c.execute("SELECT role, content FROM history WHERE session_id=? ORDER BY timestamp ASC", (session_id,))
    data = c.fetchall()
    conn.close()
    return [{"role": row[0], "content": row[1]} for row in data]

def clear_history(session_id):
    conn = sqlite3.connect('legal_chat_v4.db')
    c = conn.cursor()
    c.execute("DELETE FROM history WHERE session_id=?", (session_id,))
    conn.commit()
    conn.close()

init_db()

if "session_id" not in st.query_params:
    st.session_state.session_id = str(uuid.uuid4())
    st.query_params["session_id"] = st.session_state.session_id
else:
    st.session_state.session_id = st.query_params["session_id"]

# ==========================================
# 4. SIDEBAR - UPLOAD STABIL (CLOUD)
# ==========================================
st.sidebar.title("🗂️ Dosar Acte")

# Reset
if st.sidebar.button("🗑️ Caz Nou (Reset)", type="primary"):
    clear_history(st.session_state.session_id)
    st.session_state.messages = []
    st.session_state.uploaded_refs = [] # Resetăm referințele
    st.rerun()

st.sidebar.divider()

# Aici stocăm doar REFERINȚELE (numele fișierelor de pe serverul Google), NU fișierele propriu-zise
if "uploaded_refs" not in st.session_state:
    st.session_state.uploaded_refs = [] # Listă de dict: {'display_name': str, 'name': str (ID), 'mime_type': str}

uploaded_files = st.sidebar.file_uploader("Adaugă documente", type=["jpg", "png", "pdf"], accept_multiple_files=True)

if uploaded_files:
    if st.sidebar.button("☁️ Încarcă în Cloud"):
        progress_bar = st.sidebar.progress(0)
        
        for idx, up_file in enumerate(uploaded_files):
            # Verificăm dacă nu e deja urcat
            if not any(f['display_name'] == up_file.name for f in st.session_state.uploaded_refs):
                try:
                    with st.spinner(f"Se urcă: {up_file.name}..."):
                        # URCĂM PE GOOGLE FILE API (Nu ține memoria ocupată)
                        file_bytes = up_file.getvalue()
                        
                        # Folosim client.files.upload din noul SDK
                        # Trebuie să îi dăm un obiect file-like (BytesIO)
                        uploaded_file = client.files.upload(
                            file=BytesIO(file_bytes),
                            config=types.UploadFileConfig(
                                display_name=up_file.name,
                                mime_type=up_file.type
                            )
                        )
                        
                        # Așteptăm să fie procesat (important pt PDF-uri mari)
                        while uploaded_file.state.name == "PROCESSING":
                            time.sleep(1)
                            uploaded_file = client.files.get(name=uploaded_file.name)
                        
                        if uploaded_file.state.name == "FAILED":
                            st.sidebar.error(f"Eroare procesare Google: {up_file.name}")
                        else:
                            # Salvăm doar referința mică
                            st.session_state.uploaded_refs.append({
                                'display_name': up_file.name,
                                'name': uploaded_file.name, # Acesta e ID-ul (ex: files/xxxx)
                                'mime_type': up_file.type
                            })
                            st.sidebar.success(f"✅ {up_file.name} indexat.")
                            
                except Exception as e:
                    st.sidebar.error(f"Eroare upload: {e}")
            
            progress_bar.progress((idx + 1) / len(uploaded_files))
        
        time.sleep(1)
        st.rerun()

# Afișare Dosar
if st.session_state.uploaded_refs:
    st.sidebar.info(f"Dosar activ: {len(st.session_state.uploaded_refs)} acte")
    for f in st.session_state.uploaded_refs:
        st.sidebar.caption(f"📎 {f['display_name']}")
else:
    st.sidebar.caption("Dosar gol.")

enable_audio = st.sidebar.checkbox("🔊 Audio", value=False)

# ==========================================
# 5. CONFIGURARE & CHAT
# ==========================================

PROMPT_AVOCAT = """
Ești Avocat Expert ONRC (România). 
Analizează documentele (dacă există) și răspunde concis.
Folosește Google Search pentru verificarea taxelor/legilor la zi (2024-2025).
"""

search_tool = types.Tool(google_search=types.GoogleSearch())
generate_config = types.GenerateContentConfig(
    system_instruction=PROMPT_AVOCAT,
    tools=[search_tool],
    temperature=0.3
)

st.title("⚖️ Avocat Consultant ONRC")

if "messages" not in st.session_state or not st.session_state.messages:
    st.session_state.messages = load_history(st.session_state.session_id)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="👤" if msg["role"] == "user" else "⚖️"):
        st.markdown(msg["content"])

if user_input := st.chat_input("Întreabă avocatul..."):
    
    st.session_state.messages.append({"role": "user", "content": user_input})
    save_message(st.session_state.session_id, "user", user_input)
    with st.chat_message("user", avatar="👤"):
        st.write(user_input)

    # CONSTRUIRE PAYLOAD OPTIMIZAT
    contents_payload = []
    
    # Istoric (doar text)
    for msg in st.session_state.messages[:-1]:
        role_gemini = "model" if msg["role"] == "assistant" else "user"
        contents_payload.append(types.Content(
            role=role_gemini,
            parts=[types.Part.from_text(text=msg["content"])]
        ))
    
    # Mesaj Curent
    current_parts = []
    
    # A. Adăugăm REFERINȚELE la fișiere (URI) - Nu ocupă memorie!
    if st.session_state.uploaded_refs:
        for ref in st.session_state.uploaded_refs:
            # Magia e aici: Part.from_uri
            current_parts.append(types.Part.from_uri(
                file_uri=ref['name'], # 'name' conține URI-ul intern (files/...)
                mime_type=ref['mime_type']
            ))
        current_parts.append(types.Part.from_text(text="\n\n[Analizează documentele de mai sus]"))
    
    # B. Întrebarea
    current_parts.append(types.Part.from_text(text=user_input))
    
    contents_payload.append(types.Content(role="user", parts=current_parts))

    with st.chat_message("assistant", avatar="⚖️"):
        placeholder = st.empty()
        full_text = ""
        
        try:
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
            
            st.session_state.messages.append({"role": "assistant", "content": full_text})
            save_message(st.session_state.session_id, "assistant", full_text)

            if enable_audio:
                clean = full_text.replace("*", "")[:500]
                sound = BytesIO()
                tts = gTTS(text=clean, lang='ro')
                tts.write_to_fp(sound)
                st.audio(sound, format='audio/mp3')

        except Exception as e:
            st.error(f"Eroare: {e}")
