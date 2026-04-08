#!/usr/bin/env python3
"""
Slackbot — AI Project Team
Fast PM scope on @mention, full 4-agent analysis on #detail reply.
"""

import logging
import os
import re

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from anthropic import Anthropic

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

slack_app = App(token=os.environ["SLACK_BOT_TOKEN"])
claude = Anthropic()

# In-memory store: thread_ts -> {channel, brief, pm_output}
# Holds context for threads waiting for a #detail reply
active_threads: dict = {}


# ── Prompts ───────────────────────────────────────────────────────────────────

PM_PROMPT = """\
You are a Project Manager doing a rapid brief scope. Be fast and decisive.

Structure your response exactly like this (Slack markdown — *bold*, `code`):

*🎯 Objective*
One clear sentence — what are we building or solving?

*📦 Key Deliverables*
3–5 specific outputs, one line each

*⚠️ Risks & Blockers*
Top 3 risks, one line each

*❓ Open Questions*
What's missing before work can start? 3–5 bullets

*📊 Priority*
High / Medium / Low — one line reason

Keep it under 350 words. No waffle. No preamble.\
"""

DETAIL_PROMPT = """\
You are a virtual project team continuing analysis from a PM scope.
Run three agents sequentially, each building on the previous.

Original brief:
{brief}

PM Scope already completed:
{pm_output}

---

Now run the following three agents:

*🔍 Researcher & Analyst*
Deep technical investigation — feasibility, approaches, trade-offs, unknowns, prior art.
Thorough and evidence-based.

*📝 Report Creator*
Synthesise the PM scope and Research into a clean narrative.
Include: executive summary, key findings, recommendations.
Write for a mixed technical and leadership audience.

*🎯 Critic & Reviewer*
Push back on the team's work. What's missing? What assumptions are wrong?
What risks weren't mentioned? What needs fixing before this moves forward?
Be specific and constructive.\
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_channel_name(channel_id: str) -> str:
    try:
        result = slack_app.client.conversations_info(channel=channel_id)
        return result["channel"]["name"]
    except Exception:
        return ""


def split_chunks(text: str, max_len: int = 3900) -> list[str]:
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


def post_or_update(channel: str, ts: str, thread_ts: str, text: str, label: str = ""):
    """Update an existing message, chunking if needed."""
    if len(text) <= 3900:
        slack_app.client.chat_update(channel=channel, ts=ts, text=text)
    else:
        try:
            slack_app.client.chat_delete(channel=channel, ts=ts)
        except Exception as e:
            logger.warning(f"Could not delete placeholder: {e}")
        chunks = split_chunks(text)
        for i, chunk in enumerate(chunks):
            prefix = f"*{label}*\n\n" if (i == 0 and label) else ""
            slack_app.client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=prefix + chunk,
            )


# ── Core handlers ─────────────────────────────────────────────────────────────

def run_pm_scope(event: dict):
    """Run a quick PM scope and post it, priming the thread for #detail."""
    brief = re.sub(r"<@[A-Z0-9]+>", "", event.get("text", "")).strip()
    if not brief:
        return

    channel = event["channel"]
    thread_ts = event["ts"]

    logger.info(f"PM scope: {brief[:80]}")

    # Immediate ack
    try:
        ack = slack_app.client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="📋 *Scoping your brief...* (about 30 seconds)",
            reply_broadcast=True,
        )
        ack_ts = ack["ts"]
    except Exception as e:
        logger.error(f"Failed to post ack: {e}")
        return

    # PM agent
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": f"{PM_PROMPT}\n\nBrief:\n{brief}"}],
        )
        pm_output = resp.content[0].text
        logger.info(f"PM scope done ({len(pm_output)} chars)")
    except Exception as e:
        logger.error(f"Claude error: {e}")
        try:
            slack_app.client.chat_update(channel=channel, ts=ack_ts, text=f"❌ Error: `{e}`")
        except Exception:
            pass
        return

    # Post PM scope + #detail prompt
    try:
        full = (
            f"{pm_output}\n\n"
            "---\n"
            "_Need more detail? Reply `#detail` and I'll run the full team — "
            "Researcher, Report Creator & Critic (2–4 mins)._"
        )
        try:
            slack_app.client.chat_delete(channel=channel, ts=ack_ts)
        except Exception:
            pass
        slack_app.client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=full,
            reply_broadcast=True,
        )
        active_threads[thread_ts] = {
            "channel": channel,
            "brief": brief,
            "pm_output": pm_output,
        }
        logger.info(f"PM scope posted. Thread {thread_ts} ready for #detail")
    except Exception as e:
        logger.error(f"Failed to post PM scope: {e}")


def run_detail_analysis(event: dict):
    """Run Researcher + Report Creator + Critic for a thread that requested #detail."""
    thread_ts = event.get("thread_ts") or event["ts"]
    ctx = active_threads.get(thread_ts)
    if not ctx:
        logger.warning(f"No active thread context for {thread_ts} — ignoring #detail")
        return

    channel = ctx["channel"]
    brief = ctx["brief"]
    pm_output = ctx["pm_output"]

    logger.info(f"Detail analysis for thread {thread_ts}")

    try:
        ack = slack_app.client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=(
                "🔍 *Running full team analysis...*\n"
                "> Researcher → Report Creator → Critic\n"
                "_Takes 2–4 mins with Claude Opus. Hang tight._"
            ),
        )
        ack_ts = ack["ts"]
    except Exception as e:
        logger.error(f"Failed to post detail ack: {e}")
        return

    try:
        prompt = DETAIL_PROMPT.format(brief=brief, pm_output=pm_output)
        resp = claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        analysis = resp.content[0].text
        logger.info(f"Detail done ({len(analysis)} chars)")
    except Exception as e:
        logger.error(f"Claude error on detail: {e}")
        try:
            slack_app.client.chat_update(channel=channel, ts=ack_ts, text=f"❌ Error: `{e}`")
        except Exception:
            pass
        return

    try:
        post_or_update(channel, ack_ts, thread_ts, analysis, label="🤖 Full Team Analysis")
        del active_threads[thread_ts]
        logger.info("Detail analysis posted successfully")
    except Exception as e:
        logger.error(f"Failed to post detail analysis: {e}")


# ── Slack event handlers ──────────────────────────────────────────────────────

@slack_app.event("app_home_opened")
def handle_app_home(event, logger):
    pass  # suppress unhandled event warning


@slack_app.event("app_mention")
def handle_mention(event, logger):
    """@mention anywhere — run PM scope (unless it's a #detail reply)."""
    # Skip bot messages
    if event.get("bot_id") or event.get("subtype"):
        return
    # If it's a thread reply containing #detail, run detail
    if event.get("thread_ts") and event["thread_ts"] != event["ts"]:
        if "#detail" in event.get("text", "").lower():
            run_detail_analysis(event)
        return
    run_pm_scope(event)


@slack_app.message()
def handle_message(message, say, logger):
    """Handle messages — PM scope in #project-briefs, #detail in known threads."""
    if message.get("bot_id") or message.get("subtype"):
        return

    text = message.get("text", "").lower()
    thread_ts = message.get("thread_ts")

    if thread_ts and "#detail" in text:
        # #detail reply in a thread — check if we have context
        if thread_ts in active_threads:
            run_detail_analysis(message)
        return

    if not thread_ts:
        # Top-level message — only respond in #project-briefs
        # Skip @mentions — handle_mention already handles those
        if re.search(r"<@[A-Z0-9]+>", message.get("text", "")):
            return
        if get_channel_name(message["channel"]) == "project-briefs":
            run_pm_scope(message)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Slackbot starting (Socket Mode)...")
    handler = SocketModeHandler(slack_app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
