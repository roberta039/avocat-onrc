import streamlit as st
import google.generativeai as genai
from gtts import gTTS
from io import BytesIO
import sqlite3
import uuid
import time
import tempfile
import os

# ==========================================
# 1. CONFIGURARE PAGINĂ
# ==========================================
st.set_page_config(page_title="Avocat ONRC AI", page_icon="⚖️", layout="wide")

st.markdown("""
<style>
    .stChatMessage { font-family: 'Georgia', serif; }
    .stButton button { background-color: #2c3e50; color: white; }
    .stSpinner { color: #2c3e50; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. CONFIGURARE DB & SESIUNE
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
    st.session_state.session_id = str(uuid.uuid4())
    st.query_params["session_id"] = st.session_state.session_id
else:
    st.session_state.session_id = st.query_params["session_id"]

# ==========================================
# 3. CONFIGURARE AI & FUNCȚII UPLOAD ROBUSTE
# ==========================================
if "GOOGLE_API_KEY" in st.secrets:
    api_key = st.secrets["GOOGLE_API_KEY"]
else:
    api_key = st.sidebar.text_input("Cheie API:", type="password")

if not api_key:
    st.warning("Te rog introdu cheia API.")
    st.stop()

genai.configure(api_key=api_key)

# Helper: Upload sigur către Google (Rezolvă 504 și HttpError)
def upload_to_gemini(file_obj, mime_type):
    """
    Salvează temporar fișierul pe disc, îl urcă pe Google Servers,
    apoi șterge local. Returnează referința (URI) rapidă.
    """
    try:
        # 1. Determinăm extensia
        ext = ".pdf"
        if "image" in mime_type:
            ext = ".jpg" if "jpeg" in mime_type or "jpg" in mime_type else ".png"
            
        # 2. Creăm fișier temporar sigur
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(file_obj.getvalue())
            tmp_path = tmp.name
        
        # 3. Upload către Google (Server-to-Server, rapid)
        print(f"Uploading {tmp_path}...")
        file_ref = genai.upload_file(tmp_path, mime_type=mime_type)
        
        # 4. Curățenie locală
        os.remove(tmp_path)
        
        # 5. Așteptăm procesarea (PDF-urile mari au nevoie de 2-3 secunde)
        while file_ref.state.name == "PROCESSING":
            time.sleep(1)
            file_ref = genai.get_file(file_ref.name)
            
        return file_ref
        
    except Exception as e:
        st.error(f"Eroare Upload Intern: {e}")
        return None

# Prompt
PROMPT_AVOCAT = """
Ești un Avocat Virtual Senior, expert în Drept Comercial și proceduri ONRC (Oficiul Național al Registrului Comerțului) din România.

OBIECTIVUL TĂU:
Să oferi consultanță preliminară clară, pas cu pas, antreprenorilor care vor să înființeze, modifice sau să radieze o firmă (SRL, PFA, II, SA) în România.

BAZA LEGALĂ PE CARE O RESPECȚI:
1. Legea societăților nr. 31/1990 (actualizată).
2. OUG 44/2008 (privind PFA, II, IF).
3. Legea 265/2022 (privind digitalizarea și noile proceduri ONRC).
4. Normele metodologice privind taxele și formularele tip.

REGULI DE COMPORTAMENT STRICTE:

1. CLARITATE PROCEDURALĂ:
   - Când utilizatorul întreabă de "înființare firmă", prezintă pașii cronologici: (1) Rezervare Denumire -> (2) Redactare Act Constitutiv -> (3) Depunere Capital (dacă e cazul) -> (4) Dosar ONRC.
   - Oferă liste complete de documente necesare (C.I., dovadă sediu, acord vecini, specimen semnătură etc.).

2. SPECIFICITATE:
   - Fă distincția clară între SRL (răspundere limitată, taxe pe dividende/micro) și PFA (răspundere personală, contabilitate în partidă simplă, norme de venit).
   - Explică clar Codurile CAEN (Principal vs. Secundare).
   - Menționează portalul online (portal.onrc.ro) ca metodă principală de depunere.

3. CONTEXT FISCAL (LIMITAT):
   - Poți menționa regimul microîntreprinderilor vs. impozit pe profit, dar recomandă mereu validarea cu un contabil. Nu face calcule fiscale complexe, concentrează-te pe partea juridică.

4. DISCLAIMER OBLIGATORIU (SIGURANȚĂ JURIDICĂ):
   - La începutul sau finalul fiecărui răspuns complex, specifică: "Atenție: Acest răspuns este generat de un AI în scop informativ și nu ține loc de consultanță juridică oficială. Pentru spețe complexe sau reprezentare în instanță, contactați un avocat membru al Baroului."

5. TONUL VOCII:
   - Profesional, obiectiv, formal, dar accesibil antreprenorilor la început de drum.
   - Nu folosi jargon juridic ("latine") fără să îl explici.

EXEMPLE DE SITUAȚII:
- Dacă cineva vrea "firmă rapid", explică procedura online prin portalul ONRC.
- Dacă cineva nu are sediu, explică opțiunea de "găzduire sediu social" la avocat.
- Dacă cineva vrea PFA, întreabă dacă are calificarea necesară (diplomă) pentru codul CAEN ales.
"""

tools_config = [
    {"google_search_retrieval": {"dynamic_retrieval_config": {"mode": "dynamic", "dynamic_threshold": 0.6}}}
]

model = genai.GenerativeModel("models/gemini-2.5-flash", tools=tools_config, system_instruction=PROMPT_AVOCAT)

# ==========================================
# 4. SIDEBAR - DOSAR INTELIGENT
# ==========================================
st.sidebar.title("🗂️ Dosar Acte")

# Memorie pentru referințe fișiere (URIs)
if "dosar_uris" not in st.session_state:
    st.session_state.dosar_uris = [] # Listă de obiecte genai.File

# Reset
if st.sidebar.button("🗑️ Șterge Tot", type="primary"):
    clear_history_db(st.session_state.session_id)
    st.session_state.messages = []
    st.session_state.dosar_uris = []
    st.rerun()

st.sidebar.divider()

# Upload Widget
uploaded_files_widget = st.sidebar.file_uploader("Încarcă în Cloud", type=["jpg", "png", "pdf"], accept_multiple_files=True)

if uploaded_files_widget:
    if st.sidebar.button("☁️ Procesează și Adaugă"):
        with st.spinner("Se urcă documentele pe serverele Google..."):
            for up_file in uploaded_files_widget:
                # Verificăm duplicarea după nume (simplificat)
                if not any(f.display_name == up_file.name for f in st.session_state.dosar_uris):
                    
                    ref = upload_to_gemini(up_file, up_file.type)
                    if ref:
                        st.session_state.dosar_uris.append(ref)
                        st.sidebar.success(f"✅ {up_file.name} indexat.")
                else:
                    st.sidebar.warning(f"{up_file.name} e deja în dosar.")
        time.sleep(1)
        st.rerun()

# Afișare Dosar
if st.session_state.dosar_uris:
    st.sidebar.success(f"Dosar activ: {len(st.session_state.dosar_uris)} documente")
    for f in st.session_state.dosar_uris:
        st.sidebar.caption(f"📎 {f.display_name}") # Arată numele fișierului procesat
else:
    st.sidebar.info("Dosarul este gol.")

enable_audio = st.sidebar.checkbox("🔊 Audio", value=False)

# ==========================================
# 5. CHAT STREAMING
# ==========================================
st.title("⚖️ Avocat Consultant")

if "messages" not in st.session_state or not st.session_state.messages:
    st.session_state.messages = load_history_from_db(st.session_state.session_id)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="👤" if msg["role"] == "user" else "⚖️"):
        st.markdown(msg["content"])

if user_input := st.chat_input("Întrebare despre dosar..."):
    
    st.session_state.messages.append({"role": "user", "content": user_input})
    save_message_to_db(st.session_state.session_id, "user", user_input)
    with st.chat_message("user", avatar="👤"):
        st.write(user_input)

    # Construire Context
    # 1. Istoric Text
    history_chat = []
    for msg in st.session_state.messages[:-1]:
        history_chat.append({"role": "model" if msg["role"] == "assistant" else "user", "parts": [msg["content"]]})

    # 2. Mesaj Curent: Text + Referințe Fișiere (URI)
    # Acum trimitem doar LINK-URILE către fișiere, nu fișierele fizice. E foarte rapid.
    current_parts = [user_input]
    if st.session_state.dosar_uris:
        current_parts.extend(st.session_state.dosar_uris)
        current_parts.append("\n(Răspunde folosind documentele de mai sus)")

    with st.chat_message("assistant", avatar="⚖️"):
        placeholder = st.empty()
        full_text = ""
        
        try:
            # Pornim sesiunea
            chat = model.start_chat(history=history_chat)
            
            # STREAMING OBLIGATORIU
            response = chat.send_message(current_parts, stream=True)
            
            for chunk in response:
                if chunk.text:
                    full_text += chunk.text
                    placeholder.markdown(full_text + "▌")
            
            placeholder.markdown(full_text)
            
            # Grounding check
            try:
                if response.resolve().candidates[0].grounding_metadata.search_entry_point:
                    st.caption("🔎 Verificat online")
            except: pass

            st.session_state.messages.append({"role": "assistant", "content": full_text})
            save_message_to_db(st.session_state.session_id, "assistant", full_text)

            if enable_audio:
                clean = full_text.replace("*", "")[:500]
                sound = BytesIO()
                gTTS(text=clean, lang='ro').write_to_fp(sound)
                st.audio(sound, format='audio/mp3')

        except Exception as e:
            st.error(f"Eroare: {e}")
            if "504" in str(e):
                st.warning("⚠️ Tot a durat mult. Încearcă să urci fișiere mai mici.")
