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
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. CLIENTUL NOU GOOGLE GENAI
# ==========================================

api_key = None
# 1. Extragem din secrete
if "GOOGLE_API_KEY" in st.secrets:
    raw_key = st.secrets["GOOGLE_API_KEY"]
    if isinstance(raw_key, list):
        api_key = raw_key[0]
    else:
        api_key = raw_key

# 2. Dacă nu e în secrete, o cerem manual
if not api_key:
    api_key = st.sidebar.text_input("Introdu Google API Key:", type="password")

# 3. Stop dacă nu avem cheie
if not api_key:
    st.warning("⚠️ Te rog introdu cheia API în sidebar.")
    st.stop()

# 4. Conectare
try:
    clean_key = str(api_key).strip()
    client = genai.Client(api_key=clean_key)
except Exception as e:
    st.error(f"Eroare la conectarea cu Google AI: {e}")
    st.stop()

# ==========================================
# 3. MEMORIE SQLITE
# ==========================================
def init_db():
    conn = sqlite3.connect('legal_chat_v5.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history 
                 (session_id TEXT, role TEXT, content TEXT, timestamp REAL)''')
    conn.commit()
    conn.close()

def save_message(session_id, role, content):
    conn = sqlite3.connect('legal_chat_v5.db')
    c = conn.cursor()
    c.execute("INSERT INTO history VALUES (?, ?, ?, ?)", (session_id, role, content, time.time()))
    conn.commit()
    conn.close()

def load_history(session_id):
    conn = sqlite3.connect('legal_chat_v5.db')
    c = conn.cursor()
    c.execute("SELECT role, content FROM history WHERE session_id=? ORDER BY timestamp ASC", (session_id,))
    data = c.fetchall()
    conn.close()
    return [{"role": row[0], "content": row[1]} for row in data]

def clear_history(session_id):
    conn = sqlite3.connect('legal_chat_v5.db')
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
# 4. SIDEBAR - UPLOAD (CORECTAT URI)
# ==========================================
st.sidebar.title("🗂️ Dosar Acte")

# Reset
if st.sidebar.button("🗑️ Caz Nou (Reset)", type="primary"):
    clear_history(st.session_state.session_id)
    st.session_state.messages = []
    st.session_state.uploaded_refs = [] 
    st.rerun()

st.sidebar.divider()

if "uploaded_refs" not in st.session_state:
    st.session_state.uploaded_refs = [] # Listă de dict: {'display_name': str, 'uri': str, 'mime_type': str}

uploaded_files = st.sidebar.file_uploader("Adaugă documente (PDF/Foto)", type=["jpg", "png", "pdf"], accept_multiple_files=True)

if uploaded_files:
    if st.sidebar.button("☁️ Încarcă în Cloud"):
        progress_bar = st.sidebar.progress(0)
        
        for idx, up_file in enumerate(uploaded_files):
            if not any(f['display_name'] == up_file.name for f in st.session_state.uploaded_refs):
                try:
                    with st.spinner(f"Se urcă: {up_file.name}..."):
                        
                        # Upload
                        file_bytes = up_file.getvalue()
                        uploaded_file = client.files.upload(
                            file=BytesIO(file_bytes),
                            config=types.UploadFileConfig(
                                display_name=up_file.name,
                                mime_type=up_file.type
                            )
                        )
                        
                        # Așteptare procesare
                        while uploaded_file.state.name == "PROCESSING":
                            time.sleep(1)
                            uploaded_file = client.files.get(name=uploaded_file.name)
                        
                        if uploaded_file.state.name == "FAILED":
                            st.sidebar.error(f"Eroare Google: {up_file.name}")
                        else:
                            # --- FIX PENTRU EROAREA 400 ---
                            # Trebuie să folosim .uri (https://...), nu .name (files/...)
                            final_uri = uploaded_file.uri
                            
                            st.session_state.uploaded_refs.append({
                                'display_name': up_file.name,
                                'uri': final_uri,  # <--- AICI E CHEIA
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
# 5. CHAT LOGIC (CORECTAT URI)
# ==========================================

PROMPT_AVOCAT = """
Ești un Avocat Virtual Expert în ONRC și Legislație Comercială (România).

OBIECTIV:
Oferi consultanță juridică preliminară clară.

REGULI CRITICE:
1. GROUNDING: Folosește Google Search pentru a verifica legile din 2024-2025.
2. DOSAR: Dacă există documente atașate, analizează-le cu prioritate.
3. TON: Profesional.
4. DISCLAIMER: Info orientativă, nu consultanță oficială.
"""

search_tool = types.Tool(google_search=types.GoogleSearch())
generate_config = types.GenerateContentConfig(
    system_instruction=PROMPT_AVOCAT,
    tools=[search_tool],
    temperature=0.3
)

st.title("⚖️ Avocat Consultant ONRC")
st.caption("Verific legislația la zi prin Google Search • Analizez PDF-uri/Imagini")

if "messages" not in st.session_state or not st.session_state.messages:
    st.session_state.messages = load_history(st.session_state.session_id)

# Afișare Chat
for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="👤" if msg["role"] == "user" else "⚖️"):
        st.markdown(msg["content"])

# INPUT
if user_input := st.chat_input("Întreabă avocatul..."):
    
    # 1. Salvare Input
    st.session_state.messages.append({"role": "user", "content": user_input})
    save_message(st.session_state.session_id, "user", user_input)
    with st.chat_message("user", avatar="👤"):
        st.write(user_input)

    # 2. Construire Payload
    contents_payload = []
    
    # Istoric (doar text)
    for msg in st.session_state.messages[:-1]:
        role_gemini = "model" if msg["role"] == "assistant" else "user"
        contents_payload.append(types.Content(
            role=role_gemini,
            parts=[types.Part.from_text(text=msg["content"])]
        ))
    
    # Mesaj Curent (Fișiere + Întrebare)
    current_parts = []
    
    # A. Adăugăm REFERINȚELE (folosind URI-ul complet)
    if st.session_state.uploaded_refs:
        for ref in st.session_state.uploaded_refs:
            # --- FIX PENTRU EROAREA 400 ---
            # Folosim ref['uri'] care conține https://...
            current_parts.append(types.Part.from_uri(
                file_uri=ref['uri'], 
                mime_type=ref['mime_type']
            ))
        current_parts.append(types.Part.from_text(text="\n\n[SISTEM: Analizează documentele de mai sus din dosarul clientului.]"))
    
    # B. Întrebarea
    current_parts.append(types.Part.from_text(text=user_input))
    
    contents_payload.append(types.Content(role="user", parts=current_parts))

    # 3. Generare
    with st.chat_message("assistant", avatar="⚖️"):
        placeholder = st.empty()
        full_text = ""
        
        try:
            response_stream = client.models.generate_content_stream(
                model='gemini-1.5-flash',
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
                clean_text = full_text.replace("*", "")[:500]
                sound = BytesIO()
                tts = gTTS(text=clean_text, lang='ro')
                tts.write_to_fp(sound)
                st.audio(sound, format='audio/mp3')

        except Exception as e:
            st.error(f"Eroare comunicare AI: {e}")
