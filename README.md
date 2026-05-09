# NZ Building Code Telegram Bot

A Telegram bot that answers questions about the New Zealand Building Code using Claude AI.
Uses the NZBC reference document as its primary knowledge base, with live web search fallback to `building.govt.nz`.

---

## Features

- Answers questions on all 35 NZBC clauses (Groups A–H)
- Searches building.govt.nz automatically when the local knowledge base doesn't have enough detail
- Remembers conversation context per user (last 10 exchanges)
- Team access control via username allowlist
- `/clauses` command lists all clause groups
- `/clear` resets conversation history

---

## Setup

### Step 1: Create your Telegram bot

1. Open Telegram and message **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** you receive (looks like `123456:ABC-DEF...`)

### Step 2: Get your Anthropic API key

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an API key under **API Keys**
3. Copy it

### Step 3: Prepare the knowledge base

Copy `NZ-Building-Code-Reference.docx` into this folder, then run:

```bash
pip install python-docx
python extract_knowledge.py
```

This creates `nzbc_knowledge.txt` which the bot loads on startup.
Commit `nzbc_knowledge.txt` to your repo so Railway can use it.

### Step 4: Deploy to Railway

1. Push this folder to a GitHub repository
2. Go to [railway.app](https://railway.app) and create a new project from your repo
3. In the Railway dashboard → **Variables**, add:

| Variable | Value |
|---|---|
| `TELEGRAM_TOKEN` | Your BotFather token |
| `ANTHROPIC_API_KEY` | Your Anthropic key |
| `ALLOWED_USERNAMES` | `alice,bob,charlie` (comma-separated, no @) |

4. Railway will auto-deploy. Check **Logs** to confirm `Bot is polling...`

---

## Local development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your tokens

# Extract knowledge base (first time only)
python extract_knowledge.py

# Run the bot
python bot.py
```

---

## Project structure

```
nzbc-bot/
├── bot.py                        # Main bot — all logic lives here
├── extract_knowledge.py          # One-time script: docx → nzbc_knowledge.txt
├── nzbc_knowledge.txt            # Generated knowledge base (commit this)
├── NZ-Building-Code-Reference.docx  # Source document (optional to commit)
├── requirements.txt
├── railway.toml                  # Railway deployment config
├── .env.example                  # Template for local env vars
└── .gitignore
```

---

## Adding team members

Edit the `ALLOWED_USERNAMES` environment variable on Railway (no restart needed on next deploy):

```
ALLOWED_USERNAMES=alice,bob,charlie
```

Users must have a Telegram username set. If `ALLOWED_USERNAMES` is empty, the bot is open to anyone.

---

## Updating the knowledge base

If you update the NZBC reference document:

1. Replace `NZ-Building-Code-Reference.docx`
2. Run `python extract_knowledge.py` to regenerate `nzbc_knowledge.txt`
3. Commit and push — Railway will redeploy automatically

---

## Costs

- **Railway**: Free tier includes 500 hours/month (enough for a small team)
- **Anthropic**: Pay-per-use. claude-sonnet-4 costs ~$3/M input tokens, ~$15/M output tokens.
  A typical question costs roughly $0.01–0.03 NZD.

---

## Official NZBC sources

- Building Performance (MBIE): https://www.building.govt.nz/building-code-compliance/building-code-and-handbooks
- All clause documents: https://codehub.building.govt.nz
- Legislation: https://www.legislation.govt.nz/regulation/public/1992/0150/latest/whole.html
- Free standards: https://www.standards.govt.nz/get-standards/sponsored-standards/building-related-standards
