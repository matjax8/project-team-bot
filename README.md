# Slackbot — AI Project Team

A 4-agent AI project analysis bot that lives natively in Slack. Post a brief in `#project-briefs`, and a virtual project team analyses it in-thread.

## Agents

1. **Project Manager** — Charter, stakeholders, WBS, risks, priorities
2. **Researcher & Analyst** — Feasibility, trade-offs, technical deep-dive
3. **Report Creator** — Synthesises findings into a clean narrative
4. **Critic & Reviewer** — Pushes back on gaps, assumptions, and missed risks

## Setup

### Prerequisites
- Python 3.9+
- Slack workspace with a bot app configured (Socket Mode)
- Anthropic API key

### Install

```bash
git clone https://github.com/matjax8/project-team-bot.git
cd project-team-bot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### Configure

```bash
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_APP_TOKEN=xapp-...
export ANTHROPIC_API_KEY=sk-ant-...
python app.py
```

Or run as a systemd service — see the Slack app manifest for required scopes.

### Slack App Scopes

Import `slack-app-manifest.yaml` at api.slack.com to configure the app automatically. Required scopes: `chat:write`, `channels:history`, `channels:read`, `app_mentions:read`.

## Usage

- Post a project brief in `#project-briefs` → bot replies in-thread with full 4-agent analysis
- @mention the bot anywhere in the workspace → same analysis

## Model

Uses `claude-opus-4-6`. Change the `model` parameter in `handle_brief()` to switch.

## Related

- **AI Project Team (v3)** — Web UI version with 5 agents and streaming: [teambot.mattjack.cloud](https://teambot.mattjack.cloud)
