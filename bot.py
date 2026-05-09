"""
NZ Building Code Telegram Bot
Answers building code questions using Claude AI.
Knowledge source: embedded NZBC document + web search fallback.
"""

import os
import logging
import json
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode, ChatAction
import anthropic

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_USERNAMES = set(filter(None, os.environ.get("ALLOWED_USERNAMES", "").split(",")))
NZBC_DOC_PATH = Path(__file__).parent / "nzbc_knowledge.txt"

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

SYSTEM_PROMPT = f"""You are an expert assistant specialising in the New Zealand Building Code (NZBC).
You help building professionals, designers, and homeowners understand the NZ Building Code requirements.

Your knowledge base contains a structured summary of the NZBC (Building Regulations 1992, Schedule 1), 
administered by MBIE. When answering:

1. FIRST check the provided knowledge base below for relevant clause information.
2. If the knowledge base doesn't fully answer the question, use the web_search tool to check 
   building.govt.nz or legislation.govt.nz for current information.
3. Always cite which clause (e.g. B1, E2, H1) is relevant to the question.
4. Be precise about performance requirements, R-values, dimensions, and timeframes.
5. Note when acceptable solutions (AS) or verification methods (VM) are relevant.
6. Always recommend users verify with their Building Consent Authority (BCA) or a licensed 
   building professional for their specific project.
7. Keep answers clear and practical. Use plain language where possible.
8. If asked about something outside the Building Code scope, say so clearly.

Format responses for Telegram:
- Use *bold* for clause references and key terms
- Use short paragraphs (Telegram is a chat interface)
- End complex answers with a brief "Source" line citing the relevant clause and building.govt.nz

--- NZBC KNOWLEDGE BASE ---
{NZBC_KNOWLEDGE}
--- END KNOWLEDGE BASE ---
"""

# ── Anthropic client ──────────────────────────────────────────────────────────
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

TOOLS = [
    {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 3,
    }
]


def ask_claude(user_id: int, question: str) -> str:
    """Send question to Claude with conversation history and return answer."""
    history = conversation_history.setdefault(user_id, [])

    # Add user message
    history.append({"role": "user", "content": question})

    # Trim history to avoid token bloat (keep last N pairs)
    if len(history) > MAX_HISTORY * 2:
        history[:] = history[-(MAX_HISTORY * 2):]

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=history,
        )

        # Handle tool use loop (web search)
        while response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    log.info(f"Web search: {block.input.get('query', '')}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "Search executed.",  # Anthropic handles the actual search
                    })

            # Append assistant response + tool results and continue
            history.append({"role": "assistant", "content": response.content})
            history.append({"role": "user", "content": tool_results})

            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1500,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=history,
            )

        # Extract text from final response
        answer = " ".join(
            block.text for block in response.content if hasattr(block, "text")
        ).strip()

        # Save final assistant turn to history
        history.append({"role": "assistant", "content": answer})

        return answer or "I couldn't generate a response. Please try rephrasing your question."

    except anthropic.APIError as e:
        log.error(f"Anthropic API error: {e}")
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

    answer = ask_claude(user.id, question)

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

    log.info("Bot is polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
