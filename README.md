# VAANI 🎙️

### Voice AI Restaurant Automation System

End-to-end AI assistant for restaurant operations. Handles table reservations through natural conversation, answers menu/policy questions using RAG, sends WhatsApp confirmations with QR codes, and provides admin dashboard for booking management.

![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)
![ChromaDB](https://img.shields.io/badge/Vector%20DB-ChromaDB-FF6F00)
![Ollama](https://img.shields.io/badge/LLM-Ollama-111111)
![SQLite](https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite)

---

## ✨ Key Features

- 🎙️ **Voice-First Interface** — Natural conversation for bookings and cancellations
- 🧠 **Intent Recognition** — Slot extraction pipeline for structured reservation handling
- 📚 **RAG Knowledge Base** — ChromaDB + nomic-embed-text for menu/FAQ retrieval from PDF/TXT
- 💬 **WhatsApp Integration** — Automated confirmations/cancellations via Twilio with QR codes
- 🛡️ **Production Security** — CSRF protection, rate limiting, brute-force lockout, secure sessions
- 📊 **Admin Dashboard** — Booking management with filters, pagination, CSV export

---

## 🧠 How It Works

```
Caller turn
   |
 Router (rules, instant)
   |-- clear booking / cancel step ----> rule flow ---+
   |                                                  |--> Vaani MCP tools --> DB / WhatsApp
   '-- open-ended or multi-step -------> LLM agent ---+
                                         (MCP tool calls)
```

1. The **router** reads each turn with fast rules (dates, times, party size, names, phones).
2. Clear booking and cancel steps run on the **rule flow**: instant, no LLM needed.
3. Open-ended or multi-step requests ("table for 4 tomorrow at 8, and do you have parking?",
   "is my booking still on?", complaints) go to the **tool-calling agent** (qwen2.5:7b by default).
4. Both paths act only through the **Vaani MCP server's tools**, which enforce the rules in code:
   the agent can prepare a booking but only the caller's "yes" commits it; a cancellation needs a
   WhatsApp code.
5. When the agent only looks things up, Vaani speaks the tools' own verified answers, so the model
   cannot add facts that are not in the restaurant's records.
6. Every tool call is logged (secrets masked) and visible to owners at `/admin/agent`.

### MCP tools

| Tool | What it does | Guardrail |
|------|--------------|-----------|
| `check_availability` | Hours, party size and free tables; offers nearest times | Normalises any LLM arguments |
| `get_opening_hours` | Hours and last seating for a date | — |
| `prepare_booking` | Validates a booking and returns a read-back | Writes nothing |
| `book_table` | Makes the booking | Needs a signed hold + `caller_confirmed=true`; not offered to Vaani's agent |
| `get_booking_status` | Upcoming bookings for a number | Dates and times only, no names |
| `start_cancellation` | Finds a booking and sends a 6-digit WhatsApp code | Cancels nothing |
| `confirm_cancellation` | Cancels with the right code | Not offered to Vaani's agent |
| `query_menu_faq` | Answers from the knowledge PDF | Grounded answers only |
| `escalate_to_human` | Logs a staff handoff and notifies the owner | — |

Use the same tools from any MCP client (Claude Desktop, MCP Inspector):

```bash
python -m vaani_mcp.server
```

---

### Languages: English, Hindi, Hinglish, Kannada

```
caller audio --> Gnani Prisma (speech-to-text, detects the language every turn)
                     |
               i18n: sticky language + Hindi/Hinglish -> English (dictionary first, LLM only if needed)
                     |
               English pipeline (router, rules, agent, MCP tools) -- unchanged
                     |
               i18n: reply -> Hindi / Hinglish (hand-written sentences, LLM only for free-form ones)
                     |
               Gnani Timbre (text-to-speech) --> caller
```

- **Auto-detect:** Prisma v3.0 detects the language of every utterance among Hindi, English and Kannada
  (`GNANI_STT_LANGUAGE=hi-IN,en-IN,kn-IN`). Short replies
  ("haan", a name, a phone number) never switch the session language, so one misdetection cannot flip a call.
- **Understanding:** common booking words are converted by a dictionary (`कल`, `बजे`, `लोग`, `हाँ`,
  `kal`, `baje`, `log`, Devanagari digits, `साढ़े सात`), so most turns need no LLM. Longer Hindi sentences
  are translated by the LLM.
- **Replying:** every fixed sentence (questions, read-back, confirmations, errors) has a hand-written
  Hindi, Hinglish and Kannada version (`i18n_kn.py` for Kannada), with dates and times spoken naturally (`गुरुवार, 19 नवंबर`, `शाम 7 बजे`).
  Free-form sentences such as menu answers are LLM-translated only into languages the model writes
  well (`LLM_TRANSLATE_LANGUAGES`, default `hi,hinglish`); qwen2.5:7b writes broken Kannada, so Kannada
  callers hear those answers in English until a stronger model (e.g. Sarvam) is configured.
- **Without a Gnani key** Vaani uses the browser's speech recognition and Edge-TTS.

### Specialist agents

| Agent | Serves | Triggered by | Tools |
|-------|--------|--------------|-------|
| Router (`brain.py`) | Caller | Every turn | Rules: picks the rule flow or a specialist |
| Reservation | Caller | Availability, bookings, status, cancel, waitlist | `check_availability`, `prepare_booking`, `get_booking_status`, `start_cancellation`, `join_waitlist` |
| Concierge | Caller | Menu, prices, hours, policies; runs **in parallel** with Reservation for mixed requests | `query_menu_faq`, `get_opening_hours` |
| Care | Caller | Complaints, special requests, managers (always reaches staff) | `escalate_to_human`, `get_booking_status` |
| Analytics | Owner | Questions on `/admin/ask` | Text-to-SQL through the official **SQLite MCP server** (stdio, read-only copy) |
| Ops | Restaurant | Schedule (`python manage.py run-ops`) | Reminders, waitlist offers, feedback requests |

Each specialist only sees its own tools; tool calls are logged as `agent:<specialist>` on `/admin/agent`.

### Customer memory

- Vaani asks for the phone before the name, so returning callers hear **"Welcome back, Ravi!"** and are not
  asked their name again; "my usual table" reuses the remembered party size and time.
- Dietary needs (nut allergy, vegetarian, Jain...) and occasions (birthday, anniversary...) become booking
  notes that staff see on the dashboard; occasion offers come from `knowledge/offers.json` (owner-editable).
- First-time callers are told their preferences are remembered; **"forget me"** (also in Hindi and Kannada)
  deletes the profile.
- A full slot offers the **waitlist**; the ops agent messages waiting guests as soon as a table frees up.

### Owner analytics (MCP client)

`/admin/ask` answers questions like "Which time slots were busiest this week?". The LLM writes one SQL
query, `sqlglot` checks it (single read-only SELECT, known tables only, row limit), and it runs through the
official `mcp-server-sqlite` over stdio against a fresh analytics copy (phones masked to the last 4 digits,
no admin accounts, codes or tokens). The owner sees the answer, the rows and the exact SQL.

### Evaluation set

63 scripted conversations (22 English, 16 Hindi, 15 Hinglish, 10 Kannada, including returning callers,
the waitlist, complaints and parallel specialists) run against the real LLM, checking the booking made,
the reply language, the tools called and what was said; plus 8 owner analytics questions checked against
hand-written SQL:

```bash
python -m evals.run                     # conversations, with the model in .env
python -m evals.run --lang hi --verbose # one language, print the dialogue
python -m evals.analytics_eval          # owner analytics questions
python -m evals.run --model sarvam-30b --base-url https://api.sarvam.ai/v1
```

Results are saved to `evals/results/`. With qwen2.5:7b: conversations 63/63, analytics 8/8.

## 🏗️ Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Flask, Werkzeug |
| **AI/ML** | Ollama (qwen2.5:7b, any OpenAI-compatible LLM), ChromaDB, nomic-embed-text |
| **Agent** | Model Context Protocol (official `mcp` SDK), native tool calling |
| **Database** | SQLite with WAL mode |
| **Messaging** | Twilio WhatsApp API |
| **Voice** | Gnani Prisma v3.0 (speech-to-text), Gnani Timbre v3.0 (text-to-speech); browser STT + Edge-TTS fallback |
| **Security** | Flask-WTF (CSRF), Flask-Limiter, itsdangerous, pyotp |

---

## 📁 Project Structure

```
vaani-restaurant-automation/
├── app.py                  # Flask app factory, routes, admin, check-in
├── wsgi.py                 # Production entry point (gunicorn wsgi:app)
├── manage.py               # Admin accounts, 2FA, index rebuild (CLI)
├── brain.py                # Conversation: read-back, corrections, OTP cancel, RAG
├── agent.py                # Tool-calling LLM agent (MCP client, handoff to rule flow)
├── vaani_mcp/              # Vaani MCP server + guarded tools (tools.py, server.py)
├── llm_client.py           # One chat client: Ollama native or any OpenAI-compatible API
├── slot_rules.py           # Rule-based reading of dates, times, party size, names, phones
├── phrases.py              # Sentences shared by the rule flow, agent and tools
├── i18n.py                 # Language detection, Hindi/Hinglish/Kannada <-> English, reply localisation
├── i18n_kn.py              # Kannada language pack (sentences, booking words, numbers)
├── memory.py               # Returning callers, dietary notes, occasions, forget me
├── analytics.py            # Owner analytics agent (text-to-SQL via the SQLite MCP server)
├── ops.py                  # Ops agent: reminders, waitlist offers, feedback requests
├── evals/                  # Multilingual evaluation set (cases.json) and runner (run.py)
├── bookings.py             # Booking rules: atomic booking, cancel, restore, QR lookup
├── availability.py         # Opening hours, timezone, table assignment, alternatives
├── security.py             # Signed QR tokens, OTP codes, login lockout
├── admins.py               # Admin accounts: hashed passwords, roles, TOTP 2FA
├── database.py             # SQLite storage and migrations only
├── rag_store.py            # ChromaDB indexing + retrieval pipeline
├── config.py               # All settings, read from the environment
├── services/               # Side effects: speech (Gnani Prisma/Timbre, Edge-TTS), WhatsApp, QR images
├── tests/                  # pytest suite (no Ollama/Twilio needed)
├── Dockerfile              # Container image (gunicorn)
├── requirements.txt        # Python dependencies
├── requirements-dev.txt    # + pytest
├── .env.example            # Environment template (safe to commit)
├── knowledge/              # Restaurant docs (.pdf/.txt) — add yours here
│   └── AM_Restaurant_Knowledge.pdf
├── templates/              # HTML templates
│   ├── index.html         # Customer interface
│   ├── admin.html         # Admin dashboard
│   └── admin_login.html   # Admin authentication
├── static/                 # Static assets
│   ├── css/
│   ├── js/
├── chroma_db/             # Vector store (auto-generated, gitignored)
└── bookings.db            # SQLite database (gitignored)
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.9+
- Ollama installed and running
- Twilio account (optional, for WhatsApp features)
- ngrok (optional, for local dev with WhatsApp)

### 1. Clone repository

```bash
git clone https://github.com/ayush8771/vaani-voice-agent.git
cd vaani-voice-agent
```

### 2. Create virtual environment

```bash
python -m venv venv

# Activate
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Setup Ollama

```bash
# Download from https://ollama.ai
# Pull required models
ollama pull qwen2.5:7b
ollama pull nomic-embed-text

# Start Ollama server
ollama serve
```

### 5. Configure environment

```bash
# Copy template
cp .env.example .env

# Edit .env with your values
# Generate Flask secret key:
python -c "import secrets; print(secrets.token_hex(32))"
```

**Minimum required in `.env`:**

```env
FLASK_SECRET_KEY=your_64_char_secret_key_here
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:7b
```

Set `OPENING_HOURS` and `TABLE_SEATS` to match your restaurant (see `.env.example`).

### 5b. Create the first admin account

```bash
python manage.py create-admin --role owner
# Optional but recommended: two-factor login with an authenticator app
python manage.py enable-2fa --username YOUR_USERNAME
```

### 6. Initialize RAG knowledge base

```bash
# Ensure knowledge docs are in knowledge/ folder, then build the vector index
python manage.py build-index
```

### 7. Run application

```bash
python app.py
```

### 8. Run the tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

### Docker

```bash
docker build -t vaani .
docker run -p 8000:8000 --env-file .env -e OLLAMA_URL=http://host.docker.internal:11434/api/generate -v vaani-data:/app/data vaani
```

**Access points:**
- Customer interface: `http://localhost:5000`
- Admin dashboard: `http://localhost:5000/admin`
- Health check: `http://localhost:5000/health`

---

## 🔧 Configuration

### Core Variables (Required)

| Variable | Description | Example |
|----------|-------------|---------|
| `FLASK_SECRET_KEY` | Session and token signing key (≥32 chars) | Generate with `secrets.token_hex(32)` |
| `LLM_BASE_URL` | Any OpenAI-compatible endpoint (Ollama, Sarvam, ...) | `http://localhost:11434/v1` |
| `LLM_MODEL` | One model for agent, slot fallback and menu answers | `qwen2.5:7b` |
| `LLM_API_KEY` | API key for hosted providers | `ollama` for local |
| `AGENT_ENABLED` | Turn the tool-calling agent on or off | `true` |
| `OLLAMA_URL` | Ollama endpoint (used for knowledge-base embeddings) | `http://localhost:11434/api/generate` |

### Database & Capacity

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `bookings.db` | SQLite database file path |
| `TABLE_SEATS` | `2,2,4,4,4,6,6,8,10` | Seats per table, one number per table |
| `DINING_MINUTES` | `90` | How long a booking holds its table |
| `OPENING_HOURS` | `START_HOUR`–`END_HOUR` daily | JSON of opening windows per day; missing day = closed |
| `TIMEZONE` | `Asia/Kolkata` | Restaurant time zone |
| `LARGE_GROUP_THRESHOLD` | `10` | Larger groups are sent to private dining |
| `RATELIMIT_STORAGE_URI` | `memory://` | Use `redis://...` to share limits across workers |

### RAG Tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `RAG_MAX_DISTANCE` | `0.55` | Max cosine distance for retrieval (lower = stricter) |
| `RAG_MIN_LEXICAL_OVERLAP` | `0.08` | Min lexical overlap threshold |

### Speech: Gnani Prisma and Timbre (Optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `GNANI_API_KEY` | — | One key for both (or `GNANI_STT_KEY` / `GNANI_TTS_KEY`). Empty = browser STT + Edge-TTS |
| `GNANI_STT_MODEL` | `prisma-v3.0` | Speech-to-text model |
| `GNANI_STT_LANGUAGE` | `hi-IN,en-IN,kn-IN` | Languages to detect each turn (up to 4; `indic-auto` = all 10, misfires more) |
| `GNANI_TTS_MODEL` | `timbre-v3.0` | Text-to-speech model |
| `GNANI_TTS_VOICE` | `Shivani` | Any Timbre v3.0 voice (each speaks all 10 languages) |

### WhatsApp Integration (Optional)

| Variable | Purpose |
|----------|---------|
| `TWILIO_SID` | Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_WHATSAPP_NUMBER` | Twilio WhatsApp sender number |
| `OWNER_WHATSAPP` | Restaurant owner number (receives copies) |
| `PUBLIC_BASE_URL` | Public HTTPS URL of the app (for QR image links); `NGROK_URL` still works |

### Other Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `5000` | Flask server port |
| `FLASK_ENV` | `development` | Set `production` for secure cookies |
| `FLASK_DEBUG` | `false` | Enable debug mode (dev only) |

---

## 🧭 API Routes

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Customer voice interface |
| `/send_message` | POST | Main AI conversation endpoint |
| `/reset_state` | POST | Clear conversation state |
| `/qr/<signed>.png` | GET | QR image for a booking (unguessable signed URL) |
| `/admin/login` | GET/POST | Username, password and optional 2FA code |
| `/admin/logout` | POST | Log out |
| `/admin` | GET | Admin dashboard with filters (owners also see the audit log) |
| `/admin/cancel/<id>` | POST | Cancel booking (CSRF protected) |
| `/admin/restore/<id>` | POST | Restore cancelled booking if its table is still free |
| `/admin/export` | GET | Export bookings as CSV (owner only) |
| `/admin/checkin/<signed>` | GET/POST | Staff check-in page opened by scanning the guest's QR |
| `/admin/agent` | GET | Every tool call (rule path, agent, external MCP) and staff handoffs (owner only) |
| `/admin/ask` | GET/POST | Ask your data: plain-language analytics questions (owner only) |
| `/health` | GET | Service health check |

---

## 🎤 Using VAANI

### Customer Interface

1. Visit homepage
2. Click **"Start Call"**
3. Speak or type naturally:
   - "Book a table for 4 people tomorrow at 7 PM"
   - "What's on the menu?"
   - "Cancel my booking for June 15th"
   - "What are your opening hours?"

### Admin Dashboard

1. Navigate to `/admin/login`
2. Sign in with your username, password and (if enabled) authenticator code
3. Features:
   - View all bookings with real-time stats
   - Filter by name/phone/date/status
   - Cancel or restore bookings
   - Export filtered results to CSV (owners)
   - Scan a guest's QR to check them in
   - Audit log of admin actions (owners)
   - Pagination support

---

## 🛡️ Security Features

- ✅ **CSRF Protection** — All state-changing operations require valid tokens
- ✅ **Rate Limiting** — 300 requests/hour, 60/minute per IP
- ✅ **Admin Accounts** — Hashed passwords, owner/staff roles, optional TOTP two-factor, audit log
- ✅ **Brute-Force Protection** — Lockout after 5 failed logins, stored in the database (survives restarts)
- ✅ **Signed QR Codes** — Unguessable, tamper-proof check-in links; no QR images stored publicly
- ✅ **Verified Cancellation** — Cancelling by voice needs a 6-digit code sent to the booked WhatsApp number
- ✅ **Atomic Bookings** — Table check and insert run in one transaction, so no double-booking
- ✅ **Secure Sessions** — HTTPOnly, SameSite, Secure flags in production
- ✅ **Security Headers** — X-Content-Type-Options, X-Frame-Options, Referrer-Policy, HSTS in production
- ✅ **Input Validation** — Length caps, type validation, SQL injection prevention

---

## 🐛 Troubleshooting

### Ollama Connection Error

```bash
# Check if Ollama is running
ollama serve

# Verify models are pulled
ollama list

# Pull missing models
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

### RAG Returns No Results / Weak Answers

```bash
# Verify knowledge files exist
ls knowledge/

# Rebuild vector index
python manage.py build-index
```

### WhatsApp Messages Not Sending

- Verify Twilio credentials in `.env`
- Check `PUBLIC_BASE_URL` is set and reachable
- Ensure phone numbers include country code (e.g., `+919876543210`)
- Confirm ngrok is running if testing locally

### Database Locked Error

```bash
# SQLite WAL mode should prevent this
# If it occurs, check no other process is accessing bookings.db
# Restart the app
```

---

## 📝 Notes

- **Large Groups**: Bookings above `LARGE_GROUP_THRESHOLD` guests are redirected to private dining
- **First Run**: If `chroma_db/` is missing, the app tries to build the index on startup (needs Ollama)
- **Time Zones**: Booking rules use `TIMEZONE` (default `Asia/Kolkata`)
- **QR Codes**: Generated on request from a signed token; nothing is written to disk
- **Old databases**: New columns are added automatically on startup; old bookings have no table assigned

---

## 👤 Authors

**Mounesh S D**  
- Undergraduate AI/ML Student, Dr. AIT Bengaluru (Batch 2023-2027)

**Ayush A Waster**
  - Undergraduate AI/ML Student, Dr. AIT Bengaluru (Batch 2023-2027)
  
**Project Context**: Academic research project demonstrating AI-powered restaurant automation with RAG, NLP, and real-time integrations
---

## 📄 License

This project developed for academic/research purposes. See `LICENSE` file for details.

---

## 🙏 Acknowledgments

- **Flask** ecosystem for rapid web development
- **Ollama** and **ChromaDB** for local-first AI retrieval
- **Twilio** for WhatsApp Business API integration
- **Edge-TTS** for natural voice synthesis

---

## 🔗 Quick Links

- [Ollama Documentation](https://ollama.ai/docs)
- [ChromaDB Documentation](https://docs.trychroma.com)
- [Twilio WhatsApp API](https://www.twilio.com/docs/whatsapp)
- [Flask Documentation](https://flask.palletsprojects.com)

---

**Built with ❤️ for seamless restaurant operations**
