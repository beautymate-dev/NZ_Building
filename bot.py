"""
NZ Building Code Telegram Bot
Answers building code questions using an LLM via OpenRouter.
Knowledge source: embedded NZBC document + web search via OpenRouter ':online'.
"""

import os
import json
import math
import string
import logging
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode, ChatAction
from openai import OpenAI, OpenAIError

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.6")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://nzbuilding-production.up.railway.app")
PORT = int(os.environ.get("PORT", 8080))
ALLOWED_USERNAMES = set(filter(None, os.environ.get("ALLOWED_USERNAMES", "").split(",")))
NZBC_DOC_PATH = Path(__file__).parent / "nzbc_knowledge.txt"
NZS3604_CHUNKS_PATH = Path(__file__).parent / "nzs3604_chunks.json"

# Conversation history per user (in-memory; resets on restart)
# Format: { user_id: [{"role": ..., "content": ...}, ...] }
conversation_history: dict[int, list] = {}
MAX_HISTORY = 10  # message pairs to retain

# ── Load NZBC knowledge base ──────────────────────────────────────────────────
def load_knowledge() -> str:
    if NZBC_DOC_PATH.exists():
        return NZBC_DOC_PATH.read_text(encoding="utf-8")
    log.warning("nzbc_knowledge.txt not found — bot will rely on web search only")
    return ""

NZBC_KNOWLEDGE = load_knowledge()

# ── Load NZS 3604 chunks ──────────────────────────────────────────────────────
def load_nzs3604_chunks() -> list[dict]:
    if NZS3604_CHUNKS_PATH.exists():
        chunks = json.loads(NZS3604_CHUNKS_PATH.read_text(encoding="utf-8"))
        log.info(f"Loaded {len(chunks)} NZS 3604 chunks")
        return chunks
    log.warning("nzs3604_chunks.json not found — NZS 3604 search disabled")
    return []

NZS3604_CHUNKS = load_nzs3604_chunks()

# Pre-tokenise every chunk for fast keyword search
_STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "in", "to", "for", "is", "are",
    "be", "with", "that", "this", "it", "by", "from", "at", "as", "on",
    "not", "shall", "must", "may", "where", "when", "which", "all", "any",
    "each", "other", "than", "its", "their", "have", "has", "been",
}

def _tokenize(text: str) -> set[str]:
    text = text.lower().translate(str.maketrans("", "", string.punctuation))
    return {w for w in text.split() if w not in _STOP_WORDS and len(w) > 2}

_CHUNK_TOKENS: list[set[str]] = [_tokenize(c["content"]) for c in NZS3604_CHUNKS]

# IDF weights — rarer words score higher
_DOC_COUNT = len(_CHUNK_TOKENS) or 1
_IDF: dict[str, float] = {}
for _tokens in _CHUNK_TOKENS:
    for _t in _tokens:
        _IDF[_t] = _IDF.get(_t, 0) + 1
_IDF = {t: math.log(_DOC_COUNT / df) for t, df in _IDF.items()}


def search_nzs3604(query: str, top_n: int = 4) -> str:
    """Return the top_n most relevant NZS 3604 chunks for query, as a string."""
    if not NZS3604_CHUNKS:
        return ""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return ""
    scores = []
    for i, chunk_tokens in enumerate(_CHUNK_TOKENS):
        score = sum(_IDF.get(t, 0) for t in query_tokens & chunk_tokens)
        scores.append((score, i))
    scores.sort(reverse=True)
    top_indices = [i for score, i in scores[:top_n] if score > 0]
    if not top_indices:
        return ""
    parts = []
    for i in top_indices:
        c = NZS3604_CHUNKS[i]
        parts.append(
            f"[NZS 3604 — {c['section']} (pp. {c['pages']})]\n{c['content']}"
        )
    return "\n\n---\n\n".join(parts)

_SYSTEM_BASE = f"""You are an expert assistant specialising in New Zealand timber-framed construction \
and the New Zealand Building Code (NZBC).
You help NZ building professionals, designers, and homeowners understand NZ construction requirements.

CRITICAL RULES — NEVER BREAK THESE:
- You ONLY answer based on NEW ZEALAND standards, codes, and regulations.
- NEVER cite or reference building codes from other countries (Australia, USA, Canada, UK, etc.).
- If a question cannot be answered from the provided NZ sources, say: "I couldn't find a specific \
NZ requirement for this — please check with your BCA or a licensed building professional."

When answering, follow this priority order:

1. *NZS 3604 (Timber Framed Buildings)* — check the retrieved sections at the top of this prompt first. \
These are the most authoritative source for timber framing questions.
2. *NZBC knowledge base* — check this for performance requirements and clause references (B1, E2, H1, etc.).

Always:
- Cite the specific NZS 3604 clause or table number (e.g. "NZS 3604 Table 8.1") when available.
- Cite the relevant NZBC clause (e.g. B1, E2) where applicable.
- Be precise about dimensions, spacings, fixings, R-values, and load requirements.
- Note when acceptable solutions (AS) or verification methods (VM) apply.
- Recommend users verify with their BCA or a licensed building professional for their specific project.
- Keep answers clear and practical. Use plain language where possible.
- If a question is outside NZ timber framing and building code scope, say so clearly.

Format responses for Telegram:
- Use *bold* for clause/table references and key terms
- Use short paragraphs
- End complex answers with a brief "Source" line (e.g. "Source: NZS 3604 cl. 8.7.2 / NZBC B1")

--- NZBC KNOWLEDGE BASE ---
{NZBC_KNOWLEDGE}
--- END NZBC KNOWLEDGE BASE ---
"""


def build_system_prompt(nzs_context: str) -> str:
    """Prepend any retrieved NZS 3604 sections to the base system prompt."""
    if not nzs_context:
        return _SYSTEM_BASE
    return (
        "--- NZS 3604 RELEVANT SECTIONS (check these first) ---\n"
        + nzs_context
        + "\n--- END NZS 3604 SECTIONS ---\n\n"
        + _SYSTEM_BASE
    )

# ── OpenRouter client (OpenAI-compatible) ─────────────────────────────────────
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)


def ask_llm(user_id: int, question: str) -> str:
    """Send question to the LLM with conversation history and return answer."""
    history = conversation_history.setdefault(user_id, [])

    history.append({"role": "user", "content": question})

    if len(history) > MAX_HISTORY * 2:
        history[:] = history[-(MAX_HISTORY * 2):]

    # Retrieve relevant NZS 3604 sections for this query (cap at 20k chars)
    nzs_context = search_nzs3604(question, top_n=3)
    if len(nzs_context) > 20000:
        nzs_context = nzs_context[:20000] + "\n[... truncated for length]"
    if nzs_context:
        log.info(f"NZS 3604 context injected ({len(nzs_context)} chars)")

    def _call(system: str) -> str | None:
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            max_tokens=2000,
            messages=[
                {"role": "system", "content": system},
                *history,
            ],
        )
        choice = response.choices[0]
        finish = choice.finish_reason
        content = (choice.message.content or "").strip()
        log.info(f"OpenRouter finish_reason={finish!r} content_len={len(content)}")
        if not content:
            log.warning(f"Empty content — full choice: {choice}")
        return content or None

    try:
        answer = _call(build_system_prompt(nzs_context))

        # If the model returned nothing (e.g. tool-call mid-flight with :online),
        # retry once with a minimal system prompt so the user always gets a reply.
        if answer is None and nzs_context:
            log.warning("Empty response with NZS context — retrying without it")
            answer = _call(_SYSTEM_BASE)

        if answer is None:
            log.error("Still empty after retry — giving up")
            answer = ""

        history.append({"role": "assistant", "content": answer})
        return answer or "I couldn't generate a response. Please try rephrasing your question."

    except OpenAIError as e:
        log.error(f"OpenRouter API error: {e}")
        return "⚠️ There was an error reaching the AI service. Please try again shortly."


# ── Auth check ────────────────────────────────────────────────────────────────
def is_allowed(update: Update) -> bool:
    if not ALLOWED_USERNAMES:
        return True  # Open to all if no allowlist set
    username = update.effective_user.username or ""
    return username in ALLOWED_USERNAMES


# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        await update.message.reply_text("⛔ Sorry, you're not authorised to use this bot.")
        return

    await update.message.reply_text(
        "*NZ Building Code Assistant* 🏗️\n\n"
        "Ask me anything about the New Zealand Building Code — clause requirements, "
        "acceptable solutions, energy efficiency, structural standards, and more.\n\n"
        "I'll check my knowledge base first and search building.govt.nz if needed.\n\n"
        "_Always verify with your BCA or a licensed building professional for project-specific advice._\n\n"
        "Try asking:\n"
        "• What are the H1 insulation requirements for Climate Zone 2?\n"
        "• What does clause E2 require for weathertightness?\n"
        "• What barrier height is needed under F4?",
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*Commands*\n"
        "/start — Introduction\n"
        "/help — This message\n"
        "/clear — Clear your conversation history\n"
        "/clauses — List all NZBC clause groups\n\n"
        "Just type any building code question to get started.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    conversation_history.pop(update.effective_user.id, None)
    await update.message.reply_text("✅ Conversation history cleared.")


async def clauses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "*NZ Building Code — Clause Groups*\n\n"
        "*Group A — General*\nA1 General Objectives · A2 Definitions · A3 Building Importance Levels\n\n"
        "*Group B — Stability*\nB1 Structure · B2 Durability\n\n"
        "*Group C — Fire Safety*\nC1–C4 Fire spread, means of escape, structural stability\n\n"
        "*Group D — Access*\nD1 Access Routes · D2 Mechanical Installations\n\n"
        "*Group E — Moisture*\nE1 Surface Water · E2 External Moisture · E3 Internal Moisture\n\n"
        "*Group F — Safety of Users*\nF1–F8 Hazardous agents, falling, warning systems, signs\n\n"
        "*Group G — Services & Facilities*\nG1–G15 Hygiene, ventilation, lighting, water, drainage\n\n"
        "*Group H — Energy Efficiency*\nH1 Energy Efficiency\n\n"
        "_Ask me about any clause for details._",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        await update.message.reply_text("⛔ Sorry, you're not authorised to use this bot.")
        return

    question = update.message.text.strip()
    if not question:
        return

    user = update.effective_user
    log.info(f"Question from @{user.username} (id={user.id}): {question[:80]}")

    # Show typing indicator
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING,
    )

    answer = ask_llm(user.id, question)

    # Telegram max message length is 4096 chars; split if needed
    if len(answer) <= 4096:
        await update.message.reply_text(answer, parse_mode=ParseMode.MARKDOWN)
    else:
        for i in range(0, len(answer), 4000):
            await update.message.reply_text(answer[i:i+4000], parse_mode=ParseMode.MARKDOWN)


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("Starting NZ Building Code Bot...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("clauses", clauses))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info(f"Starting webhook on port {PORT} → {WEBHOOK_URL}")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=WEBHOOK_URL,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
