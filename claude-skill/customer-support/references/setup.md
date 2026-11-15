# Setup and Operating Modes

## What this Skill is

A set of instructions and reference documents that teach Claude the Northstar
Supply support workflow: investigate a ticket, apply official policies, decide
on escalation, and draft a reply for a human to send. Northstar Supply and all
of its data are fictional.

The same policies and workflows power the Python agent in the source
repository (`Week-8-Day-1-AI-Customer-Support-Agent`). The build script copies
them into this package so the two never disagree.

## Package layout

```
customer-support/
├── SKILL.md                         ← loaded when the Skill triggers
└── references/
    ├── setup.md                     ← this file
    ├── tool-configuration.md        ← edit to describe YOUR connectors (Mode B)
    ├── conversation-workflows.md    ← one-page summary of all workflows
    ├── example-scenarios.md         ← test messages + expected behavior
    ├── GENERATED.md                 ← explains the copied folders below
    ├── knowledge-base/              ← policies, FAQs, procedures (copied)
    ├── workflows/                   ← detailed procedures (copied)
    └── sample-data/                 ← FICTIONAL orders, products, tickets (copied)
```

## Mode A — no connectors (default)

What works:

- Pasting a customer message and getting intent, sentiment, escalation, and a draft.
- Policy questions answered from `knowledge-base/policies/`.
- Walking through the sample scenarios using `sample-data/` (orders #1041–#1051,
  tickets 4990 and 5001–5013). Results are labeled **[SAMPLE DATA]**.
- Using order details *you paste* into the chat (labeled "provided by user").

What does not work:

- Looking up real orders, tracking, or tickets. Claude must say it cannot.
- Sending replies, refunds, or any action — by design, in either mode.

Tip: Claude reads JSON from `sample-data/` when asked about a sample order. If
your Claude environment has code execution, it may also read the files with a
script; otherwise it reads them directly.

## Mode B — connectors configured

1. Give Claude real access to your systems — for example a Shopify or Zendesk
   MCP connector added in Claude's settings, or another tool your organization
   provides. **Writing a tool name in Markdown does not create a tool.**
2. Edit `references/tool-configuration.md` so it names the tools you actually
   have and maps them to the steps of the workflow.
3. Rebuild/re-upload the Skill (or edit it in place if your Claude plan allows).
4. Test with a real, low-risk ticket and confirm Claude cites the tool it used.

Use read-only credentials. The workflows never need write access, and keeping
the connector read-only means a manipulated conversation cannot cause harm.

## Installing in Claude

1. Download the ZIP from https://github.com/walexfigura/Week-8-Day-1-AI-Customer-Support-Agent/raw/main/claude-skill/customer-support-skill.zip, or in the source repository run `python run.py build-skill` → `dist/customer-support-skill.zip`.
2. In Claude, open **Settings → Capabilities** and make sure Skills (and code
   execution, if your plan requires it for Skills) are enabled.
3. Under **Skills**, choose **Upload skill** and select the ZIP.
4. Start a new chat and paste a test message from `example-scenarios.md`.

Menu names can change; if you can't find the option, search Claude's help
center for "custom skills". Custom Skills are available on paid plans and are
uploaded per user.

## Customizing for your own business

| To change | Edit (in the source repository) | Then |
|---|---|---|
| Company name and voice | `claude-skill/customer-support/SKILL.md` | rebuild |
| Return/shipping/warranty rules | `knowledge-base/policies/*.md` (text **and** front matter numbers) | run tests, rebuild |
| Escalation conditions | `agent/workflows/escalation.md`, `knowledge-base/procedures/escalation-guide.md` | rebuild |
| Your tools | `claude-skill/customer-support/references/tool-configuration.md` | rebuild |
| Example messages | `claude-skill/customer-support/references/example-scenarios.md` | rebuild |

Replace the fictional sample data before using the Skill with real teams, or
remove `sample-data/` from the build if you only want Mode B.
