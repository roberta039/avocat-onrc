import streamlit as st
from google import genai
from google.genai import types
from PIL import Image
from gtts import gTTS
from io import BytesIO
import sqlite3
import uuid
import time
import re  # <--- NOU: Pentru curățarea etichetelor HTML din Word
from docx import Document
from docx.shared import Pt

# ==========================================
# 1. CONFIGURARE PAGINĂ & STIL
# ==========================================
st.set_page_config(
    page_title="Avocat ONRC AI (2025)",
    page_icon="⚖️",
    layout="wide"
)

st.markdown("""
<style>
    .stChatMessage { font-family: 'Georgia', serif; font-size: 1.05rem; }
    .stButton button { background-color: #2c3e50; color: white; border-radius: 5px; }
    .stSuccess { background-color: #f0fdf4; border: 1px solid #bbf7d0; color: #166534; }
    h1 { color: #1e3a8a; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. CLIENTUL GOOGLE GENAI (ROBUST)
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
    st.warning("⚠️ Te rog introdu cheia API în sidebar.")
    st.stop()

try:
    clean_key = str(api_key).strip()
    client = genai.Client(api_key=clean_key)
except Exception as e:
    st.error(f"Eroare critică la conectare: {e}")
    st.stop()

# ==========================================
# 3. MEMORIE (SQLITE)
# ==========================================
def init_db():
    conn = sqlite3.connect('legal_chat_clean.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history 
                 (session_id TEXT, role TEXT, content TEXT, timestamp REAL)''')
    conn.commit()
    conn.close()

def save_message(session_id, role, content):
    conn = sqlite3.connect('legal_chat_clean.db')
    c = conn.cursor()
    c.execute("INSERT INTO history VALUES (?, ?, ?, ?)", (session_id, role, content, time.time()))
    conn.commit()
    conn.close()

def load_history(session_id):
    conn = sqlite3.connect('legal_chat_clean.db')
    c = conn.cursor()
    c.execute("SELECT role, content FROM history WHERE session_id=? ORDER BY timestamp ASC", (session_id,))
    data = c.fetchall()
    conn.close()
    return [{"role": row[0], "content": row[1]} for row in data]

def clear_history(session_id):
    conn = sqlite3.connect('legal_chat_clean.db')
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
# 4. GENERATOR DOCUMENTE WORD (CURĂȚARE HTML)
# ==========================================
def create_docx(text):
    doc = Document()
    
    # --- PAS 1: Curățare HTML (Citations & Tags) ---
    # Eliminăm blocul de citații <details>...</details>
    clean_text = re.sub(r'<details>.*?</details>', '', text, flags=re.DOTALL)
    
    # Înlocuim <br> cu linii noi
    clean_text = clean_text.replace('<br>', '\n').replace('<br/>', '\n')
    
    # Eliminăm alte tag-uri HTML reziduale
    clean_text = re.sub(r'<[^>]+>', '', clean_text)

    # --- PAS 2: Formatare Word ---
    lines = clean_text.split('\n')
    
    for line in lines:
        stripped_line = line.strip()
        if not stripped_line:
            continue
            
        # Titluri Markdown (#)
        if stripped_line.startswith('#'):
            clean_content = stripped_line.replace('#', '').strip()
            doc.add_heading(clean_content, level=1)
        
        # Liste Markdown (- sau *)
        elif stripped_line.startswith('- ') or stripped_line.startswith('* '):
            clean_content = stripped_line[2:].strip().replace('**', '').replace('__', '')
            doc.add_paragraph(clean_content, style='List Bullet')
            
        # Text normal
        else:
            clean_content = stripped_line.replace('**', '').replace('__', '')
            doc.add_paragraph(clean_content)
            
    bio = BytesIO()
    doc.save(bio)
    bio.seek(0)
    return bio

# ==========================================
# 5. SIDEBAR - UPLOAD CLOUD
# ==========================================
st.sidebar.title("🗂️ Dosar Acte")

if st.sidebar.button("🗑️ Caz Nou (Reset)", type="primary"):
    clear_history(st.session_state.session_id)
    st.session_state.messages = []
    st.session_state.uploaded_refs = [] 
    st.rerun()

st.sidebar.divider()

if "uploaded_refs" not in st.session_state:
    st.session_state.uploaded_refs = []

uploaded_files = st.sidebar.file_uploader("Adaugă documente", type=["jpg", "png", "pdf"], accept_multiple_files=True)

if uploaded_files:
    if st.sidebar.button("☁️ Încarcă în Dosar"):
        progress_bar = st.sidebar.progress(0)
        
        for idx, up_file in enumerate(uploaded_files):
            if not any(f['display_name'] == up_file.name for f in st.session_state.uploaded_refs):
                try:
                    with st.spinner(f"Se procesează: {up_file.name}..."):
                        file_bytes = up_file.getvalue()
                        uploaded_file = client.files.upload(
                            file=BytesIO(file_bytes),
                            config=types.UploadFileConfig(
                                display_name=up_file.name,
                                mime_type=up_file.type
                            )
                        )
                        
                        while uploaded_file.state.name == "PROCESSING":
                            time.sleep(1)
                            uploaded_file = client.files.get(name=uploaded_file.name)
                        
                        if uploaded_file.state.name == "FAILED":
                            st.sidebar.error(f"Eroare Google: {up_file.name}")
                        else:
                            st.session_state.uploaded_refs.append({
                                'display_name': up_file.name,
                                'uri': uploaded_file.uri, 
                                'mime_type': up_file.type
                            })
                            st.sidebar.success(f"✅ {up_file.name} adăugat.")
                            
                except Exception as e:
                    st.sidebar.error(f"Eroare upload: {e}")
            progress_bar.progress((idx + 1) / len(uploaded_files))
        time.sleep(1)
        st.rerun()

if st.session_state.uploaded_refs:
    st.sidebar.info(f"Dosar activ: {len(st.session_state.uploaded_refs)} acte")
else:
    st.sidebar.caption("Dosar gol.")

enable_audio = st.sidebar.checkbox("🔊 Audio", value=False)

# ==========================================
# 6. CONFIGURARE AVOCAT
# ==========================================

PROMPT_AVOCAT = """
Ești un Avocat Virtual Senior, Expert în ONRC, Drept Comercial și Fiscalitate (România).

OBIECTIV PRINCIPAL:
Să oferi consultanță juridică preliminară clară și să redactezi acte complete.

REGULI DE AUR (PROCEDURĂ DE LUCRU):

1. GROUNDING (Verificare Legislativă):
   - FOLOSEȘTE ACTIV GOOGLE SEARCH pentru a verifica legile valabile în 2023-2026.
   - Caută specific în Monitorul Oficial sau pe onrc.ro (ex: Legea 265/2022).

2. REDACTARE DOCUMENTE (CRITIC):
   - Când utilizatorul cere "redactează", "scrie" sau "fă-mi un act":
   - NU face rezumate.
   - Scrie TEXTUL COMPLET al actului, formal, cu articole (Art. 1, Art. 2...).
   - Folosește titluri Markdown (# TITLU) pentru formatare.

3. ANALIZA DOSARULUI:
   - Analizează documentele încărcate cu prioritate.

4. DISCLAIMER:
   - Menționează discret că ești un AI și informațiile sunt orientative.
"""

search_tool = types.Tool(google_search=types.GoogleSearch())
generate_config = types.GenerateContentConfig(
    system_instruction=PROMPT_AVOCAT,
    tools=[search_tool],
    temperature=0.3
)

# ==========================================
# 7. INTERFAȚĂ CHAT & LOGICĂ
# ==========================================

st.title("⚖️ Avocat Consultant ONRC")
st.caption("Expertiză juridică 2024-2025 • Redactare Acte • Analiză Dosar")

if "messages" not in st.session_state or not st.session_state.messages:
    st.session_state.messages = load_history(st.session_state.session_id)

# Afișare Mesaje
for i, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"], avatar="👤" if msg["role"] == "user" else "⚖️"):
        st.markdown(msg["content"])
        
        if msg["role"] == "assistant":
            # Word-ul va fi curat (fără <details> etc)
            docx = create_docx(msg["content"])
            st.download_button(
                label="📄 Descarcă Word (.docx)",
                data=docx,
                file_name=f"Document_Juridic_{i}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key=f"dl_{i}"
            )

# Input
if user_input := st.chat_input("Ex: Redactează Decizia Asociatului Unic pentru schimbare sediu..."):
    
    st.session_state.messages.append({"role": "user", "content": user_input})
    save_message(st.session_state.session_id, "user", user_input)
    with st.chat_message("user", avatar="👤"):
        st.write(user_input)

    # Context
    contents_payload = []
    for msg in st.session_state.messages[:-1]:
        role_gemini = "model" if msg["role"] == "assistant" else "user"
        contents_payload.append(types.Content(
            role=role_gemini,
            parts=[types.Part.from_text(text=msg["content"])]
        ))
    
    current_parts = []
    if st.session_state.uploaded_refs:
        for ref in st.session_state.uploaded_refs:
            current_parts.append(types.Part.from_uri(
                file_uri=ref['uri'], 
                mime_type=ref['mime_type']
            ))
        current_parts.append(types.Part.from_text(text="\n\n[SISTEM: Analizează documentele de mai sus]"))
    current_parts.append(types.Part.from_text(text=user_input))
    
    contents_payload.append(types.Content(role="user", parts=current_parts))

    # Generare
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

            # Download imediat
            docx = create_docx(full_text)
            st.download_button(
                label="📄 Descarcă Documentul Word (.docx)",
                data=docx,
                file_name="Document_Juridic_AI.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="dl_new"
            )

            if enable_audio:
                # Curățăm audio de simboluri și HTML
                clean_text_audio = re.sub(r'<[^>]+>', '', full_text)
                clean_text_audio = clean_text_audio.replace("*", "").replace("#", "")[:600]
                
                sound = BytesIO()
                tts = gTTS(text=clean_text_audio, lang='ro')
                tts.write_to_fp(sound)
                st.audio(sound, format='audio/mp3')

        except Exception as e:
            st.error(f"Eroare: {e}")
