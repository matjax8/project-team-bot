"""
Project Team Bot — Slack bot powered by Claude
================================================
When someone posts a brief in #project-briefs, this bot runs it through
a virtual 4-agent project team (PM, Researcher, Report Creator, Critic)
and replies in the thread with the full analysis.

Requires:
  SLACK_BOT_TOKEN    — xoxb-... token from your Slack app
  SLACK_APP_TOKEN    — xapp-... token (for Socket Mode)
  ANTHROPIC_API_KEY  — from console.anthropic.com
"""

import os
import re
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from anthropic import Anthropic

# ── Clients ──────────────────────────────────────────────────────────────────

slack_app = App(token=os.environ["SLACK_BOT_TOKEN"])
claude    = Anthropic()  # reads ANTHROPIC_API_KEY from environment

# ── Project Team System Prompt ───────────────────────────────────────────────

SYSTEM_PROMPT = """
You are orchestrating a virtual project team of 4 AI agents for software and tech projects.
Each agent has a distinct role, personality, and focus area. They work sequentially,
each building on the previous agent's output.

THE TEAM:

1. Project Manager (PM)
   Focus: Structure, planning, priorities, risks, timelines.
   Read the brief and produce: core objective, key deliverables, risks/blockers,
   priority order, and open questions before work begins.
   Voice: Direct, organised, action-oriented.

2. Researcher / Analyst
   Focus: Deep technical investigation, trade-off analysis, context.
   Build on the PM's plan: investigate feasibility, compare approaches,
   surface relevant patterns and prior art, flag unknowns.
   Voice: Thoughtful, thorough, evidence-based.

3. Report Creator
   Focus: Synthesis, clear communication.
   Synthesise the PM and Researcher into a clean narrative with executive summary,
   key findings, and recommendations. Write for a mixed technical/leadership audience.
   Voice: Clear, professional, readable.

4. Critic / Reviewer
   Focus: Quality assurance, devil's advocate.
   Push back on the team's output: what's missing from the plan, gaps in the analysis,
   assumptions that might be wrong, risks nobody mentioned.
   Voice: Constructive but direct. Specific about what needs fixing and why.

FORMAT YOUR RESPONSE LIKE THIS (use Slack markdown — *bold*, _italic_, `code`):

*🗂 Project Manager*
[PM analysis here]

---

*🔍 Researcher / Analyst*
[Research analysis here]

---

*✍️ Report Creator*
[Report summary here]

---

*🔎 Critic / Reviewer*
[Critique here]

---

*📋 Team Summary*
Top 3 takeaways, key open questions, and concrete next steps.

---

*📅 Gantt Chart*
After the Team Summary, always produce a simple Gantt chart showing the project timeline.
Use a code block so it renders in monospace in Slack. Format it like this example:

```
Task                        Wk1  Wk2  Wk3  Wk4
─────────────────────────── ──── ──── ──── ────
Planning & Requirements     ████ ░░░░ ░░░░ ░░░░
Architecture & Design       ░░░░ ████ ░░░░ ░░░░
Development                 ░░░░ ░░██ ████ ░░░░
Testing & QA                ░░░░ ░░░░ ░░██ ██░░
Deployment & Docs           ░░░░ ░░░░ ░░░░ ████
```

Rules for the Gantt:
- Derive the tasks and timeline from the PM's plan — make it specific to THIS project.
- Use ████ for active work, ░░░░ for idle, and label each row with the actual task name.
- Always show weeks (Wk1, Wk2, etc.) relative to project start.
- If the project spans more or fewer than 4 weeks, adjust the columns accordingly.
- Keep task names concise (max 28 chars) so the chart stays aligned.
- For a "quick take" request, skip the Gantt.

IMPORTANT RULES:
- If the brief is vague, the PM should flag this and ask clarifying questions,
  but still attempt an analysis with stated assumptions.
- If the user asks for a "quick take", compress each agent to 2-4 sentences and skip the Gantt.
- Keep each agent's voice distinct. The Critic should raise points the others missed.
- Always end with the Team Summary followed by the Gantt Chart.
- Format for Slack: use *bold* for headers, avoid markdown # headers, keep lines readable.
""".strip()

# ── Bot User ID (to avoid responding to ourselves) ────────────────────────────

BOT_USER_ID = None

@slack_app.event("app_home_opened")
def handle_app_home_opened(event, logger):
    pass  # required to avoid unhandled event warnings

def get_bot_user_id():
    global BOT_USER_ID
    if BOT_USER_ID is None:
        result = slack_app.client.auth_test()
        BOT_USER_ID = result["user_id"]
    return BOT_USER_ID

# ── Core handler ─────────────────────────────────────────────────────────────

@slack_app.event("app_mention")
def handle_mention(event, say, logger):
    """Fires when someone @mentions the bot in any channel."""
    # Reuse the same logic as handle_message
    handle_brief(event, say, logger)

def handle_brief(event, say, logger):
    """Core logic — handles a project brief from either a message or @mention."""

    # Skip messages from bots (including ourselves)
    if event.get("bot_id") or event.get("subtype"):
        return

    # Skip if this is already a thread reply
    if event.get("thread_ts") and event["thread_ts"] != event["ts"]:
        return

    brief = event.get("text", "").strip()
    if not brief:
        return

    # Strip any @mentions from the text
    brief = re.sub(r"<@[A-Z0-9]+>", "", brief).strip()
    if not brief:
        return

    channel = event["channel"]
    thread_ts = event["ts"]

    logger.info(f"Running project team on brief: {brief[:80]}...")

    # Post a "thinking" message in the thread
    thinking_response = slack_app.client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text="🤔 *Project Team is reviewing this brief...* (this takes ~20 seconds)",
    )

    # Call Claude
    try:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": f"Project brief:\n\n{brief}"}
            ],
        )
        analysis = response.content[0].text
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        slack_app.client.chat_update(
            channel=channel,
            ts=thinking_response["ts"],
            text=f"❌ Sorry, the team ran into an error: `{str(e)}`",
        )
        return

    # Update the thinking message with the real analysis
    if len(analysis) <= 3900:
        slack_app.client.chat_update(
            channel=channel,
            ts=thinking_response["ts"],
            text=analysis,
        )
    else:
        slack_app.client.chat_delete(
            channel=channel,
            ts=thinking_response["ts"],
        )
        chunks = split_into_chunks(analysis, 3900)
        for i, chunk in enumerate(chunks):
            prefix = "*🤖 Virtual Project Team Analysis*\n\n" if i == 0 else ""
            slack_app.client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=prefix + chunk,
            )

    logger.info("Analysis posted successfully.")


@slack_app.message()
def handle_message(message, say, logger):
    """Fires on regular messages — only responds in #project-briefs."""
    channel_name = get_channel_name(message["channel"])
    if channel_name != "project-briefs":
        return
    handle_brief(message, say, logger)


def get_channel_name(channel_id: str) -> str:
    """Look up the channel name from its ID."""
    try:
        result = slack_app.client.conversations_info(channel=channel_id)
        return result["channel"]["name"]
    except Exception:
        return ""


def split_into_chunks(text: str, max_len: int) -> list[str]:
    """Split text at the nearest paragraph break before max_len."""
    chunks = []
    while len(text) > max_len:
        split_at = text.rfind("\n\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()
    if text:
        chunks.append(text)
    return chunks


# ── Entry point ───────────────────────────────────────────────────────────────

def start_health_server():
    """Tiny web server so Render's Web Service health check passes."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Project Team Bot is running.")
        def log_message(self, format, *args):
            pass  # silence access logs
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

if __name__ == "__main__":
    print("🚀 Project Team Bot starting (Socket Mode)...")
    # Start health check server in background so Render stays happy
    threading.Thread(target=start_health_server, daemon=True).start()
    handler = SocketModeHandler(slack_app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
