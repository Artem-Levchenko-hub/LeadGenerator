"""JSON-схемы tools для Anthropic tool use API.

Эти dict'ы передаются в `tools=[...]` параметр messages.create().
Каждый tool имеет соответствующий handler в `worker/agents/tools/handlers.py`.
"""
from __future__ import annotations


FETCH_SITE = {
    "name": "fetch_site",
    "description": (
        "Fetches a website and returns a structured analysis: HTML title, "
        "meta description, headers, detected CMS, presence of HTTPS, viewport "
        "meta (mobile-friendly), Open Graph, contact form/phone visibility, "
        "load time. Use this as the FIRST step when analyzing a lead's site "
        "to find weaknesses for the cold outreach."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Full URL with scheme (https://example.com)",
            },
        },
        "required": ["url"],
    },
}

DNS_CHECK = {
    "name": "dns_check",
    "description": (
        "Checks DNS records of a domain: presence of SPF, DKIM, DMARC, MX "
        "records. Useful to detect 'weak_dmarc' or email-deliverability issues."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "domain": {"type": "string", "description": "Bare domain, no scheme"},
        },
        "required": ["domain"],
    },
}

WHOIS_LOOKUP = {
    "name": "whois_lookup",
    "description": (
        "Returns domain age, registrar, expiration date. Useful to assess "
        "whether the business has been online long (age) and may have an "
        "outdated stack."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "domain": {"type": "string"},
        },
        "required": ["domain"],
    },
}

RECORD_WEAKNESS = {
    "name": "record_weakness",
    "description": (
        "Records a found weakness in the lead_weaknesses table. Use this "
        "for EACH weakness you find — they will be referenced in the outreach "
        "email. Style of observation_text: factual, no pressure ('site has no "
        "HTTPS — browsers show a warning'), NOT salesy."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "company_id": {"type": "integer"},
            "kind": {
                "type": "string",
                "description": "One of the kinds from WEAKNESSES_TAXONOMY",
            },
            "severity": {"type": "string", "enum": ["low", "med", "high"]},
            "evidence_url": {"type": "string"},
            "observation_text": {"type": "string"},
            "suggested_fix": {"type": "string"},
            "est_impact": {
                "type": "string",
                "description": "Bottom-line impact for the client's business",
            },
        },
        "required": ["company_id", "kind", "severity", "observation_text"],
    },
}

DRAFT_MESSAGE = {
    "name": "draft_message",
    "description": (
        "Adds a message to outbox with status='draft'. Auditor will validate "
        "and (if approved) move to 'holding' for the cooling-off period before "
        "actual send. NEVER tries to send directly. Use channel='email' for "
        "cold outreach (the only supported channel in Sprint 1)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "company_id": {"type": "integer"},
            "channel": {
                "type": "string",
                "enum": ["email", "telegram", "sms"],
            },
            "to_address": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string", "description": "Plain text body"},
            "conversation_id": {
                "type": "integer",
                "description": "Optional. If continuing a thread, set this.",
            },
        },
        "required": ["company_id", "channel", "to_address", "body"],
    },
}

READ_THREAD = {
    "name": "read_thread",
    "description": (
        "Returns the full message history of a conversation thread (in/out). "
        "Use this when handling 'outreach.continue' to understand context "
        "before replying."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "conversation_id": {"type": "integer"},
        },
        "required": ["conversation_id"],
    },
}

UPDATE_COMPANY = {
    "name": "update_company",
    "description": (
        "Updates Company row fields (notes, industry refinement, contacts JSON). "
        "Use sparingly — for adding info you discovered during analysis."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "company_id": {"type": "integer"},
            "fields": {
                "type": "object",
                "description": "Dict of field:value to update",
            },
        },
        "required": ["company_id", "fields"],
    },
}

UPDATE_CONVERSATION_STATE = {
    "name": "update_conversation_state",
    "description": (
        "Changes Conversation.state. Common transitions: 'engaged' → "
        "'qualifying' (Sales picks up), 'qualifying' → 'ready_for_proposal' "
        "(Proposal Agent picks up), → 'lost'/'won'/'stalled'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "conversation_id": {"type": "integer"},
            "state": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["conversation_id", "state"],
    },
}

ESCALATE_TO_HUMAN = {
    "name": "escalate_to_human",
    "description": (
        "Sets Company.needs_human=true and (if conversation_id given) "
        "Conversation.state='needs_human'. Use when: hard objection, request "
        "for meeting, contract-ready, loop guard (5+ bot msgs without reply), "
        "or any unclear situation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "company_id": {"type": "integer"},
            "reason": {"type": "string"},
            "conversation_id": {"type": "integer"},
        },
        "required": ["company_id", "reason"],
    },
}

FINISH = {
    "name": "finish",
    "description": (
        "MUST be called as the LAST tool to end the run. Provide a 1–2 "
        "sentence summary of what you did."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
        },
        "required": ["summary"],
    },
}


# Группа tools для Outreach Agent.
OUTREACH_TOOLS = [
    FETCH_SITE, DNS_CHECK, WHOIS_LOOKUP,
    RECORD_WEAKNESS, DRAFT_MESSAGE, READ_THREAD,
    UPDATE_COMPANY, UPDATE_CONVERSATION_STATE,
    ESCALATE_TO_HUMAN, FINISH,
]
