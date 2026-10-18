"""Gmail skills for Tesseract - read, send, search, and manage emails."""

import base64
import logging
from typing import Any

from skills.skill_base import SkillBase, SkillResult

logger = logging.getLogger(__name__)


def _get_gmail_service():
    """Get authenticated Gmail service from OAuth manager."""
    from auth.google_oauth import get_google_oauth_manager
    oauth = get_google_oauth_manager()
    return oauth.get_gmail_service()


def _format_message(msg: dict, include_body: bool = False) -> dict:
    """Format a Gmail message for display."""
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    result = {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "subject": headers.get("Subject", "(No Subject)"),
        "from": headers.get("From", "Unknown"),
        "to": headers.get("To", ""),
        "date": headers.get("Date", ""),
        "snippet": msg.get("snippet", ""),
        "label_ids": msg.get("labelIds", []),
    }
    if include_body:
        result["body"] = _extract_body(msg.get("payload", {}))
    return result


def _extract_body(payload: dict) -> str:
    """Extract plain text body from Gmail message payload."""
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
            elif part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
            elif "parts" in part:
                result = _extract_body(part)
                if result:
                    return result
    else:
        if payload.get("mimeType") == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    return ""


class GmailReadInboxSkill(SkillBase):
    """Skill to read recent emails from Gmail inbox."""

    @property
    def name(self) -> str:
        return "gmail.read_inbox"

    @property
    def description(self) -> str:
        return "Read recent emails from Gmail inbox. Returns subject, sender, date, and snippet."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of emails to return (default: 10, max: 50)",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 50,
                },
                "query": {
                    "type": "string",
                    "description": "Gmail search query (e.g., 'is:unread', 'from:example@gmail.com', 'subject:meeting')",
                    "default": "in:inbox",
                },
                "include_body": {
                    "type": "boolean",
                    "description": "Whether to include full email body",
                    "default": False,
                },
            },
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        max_results = kwargs.get("max_results", 10)
        query = kwargs.get("query", "in:inbox")
        include_body = kwargs.get("include_body", False)

        try:
            service = _get_gmail_service()

            # List messages
            results = service.users().messages().list(
                userId="me",
                q=query,
                maxResults=max_results,
            ).execute()

            messages = results.get("messages", [])
            emails = []

            for msg in messages:
                full_msg = service.users().messages().get(
                    userId="me",
                    id=msg["id"],
                    format="full" if include_body else "metadata",
                    metadataHeaders=["Subject", "From", "To", "Date"] if not include_body else None,
                ).execute()
                emails.append(_format_message(full_msg, include_body))

            return SkillResult.success({"emails": emails, "count": len(emails)})
        except Exception as e:
            logger.exception("Error reading inbox")
            return SkillResult.failure(f"Error reading inbox: {e}")


class GmailSendEmailSkill(SkillBase):
    """Skill to send an email via Gmail."""

    @property
    def name(self) -> str:
        return "gmail.send_email"

    @property
    def description(self) -> str:
        return "Send an email via Gmail. Requires to, subject, and body."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject",
                },
                "body": {
                    "type": "string",
                    "description": "Email body (plain text)",
                },
                "cc": {
                    "type": "string",
                    "description": "CC recipients (comma-separated)",
                    "default": "",
                },
                "bcc": {
                    "type": "string",
                    "description": "BCC recipients (comma-separated)",
                    "default": "",
                },
            },
            "required": ["to", "subject", "body"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        to = kwargs.get("to", "")
        subject = kwargs.get("subject", "")
        body = kwargs.get("body", "")
        cc = kwargs.get("cc", "")
        bcc = kwargs.get("bcc", "")

        if not to or not subject or not body:
            return SkillResult.failure("to, subject, and body are required")

        try:
            service = _get_gmail_service()

            # Create message
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            message = MIMEMultipart()
            message["to"] = to
            if cc:
                message["cc"] = cc
            if bcc:
                message["bcc"] = bcc
            message["subject"] = subject
            message.attach(MIMEText(body, "plain"))

            # Encode
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

            # Send
            sent = service.users().messages().send(
                userId="me",
                body={"raw": raw},
            ).execute()

            return SkillResult.success({
                "message_id": sent.get("id"),
                "thread_id": sent.get("threadId"),
                "status": "sent",
            })
        except Exception as e:
            logger.exception("Error sending email")
            return SkillResult.failure(f"Error sending email: {e}")


class GmailSearchSkill(SkillBase):
    """Skill to search Gmail emails."""

    @property
    def name(self) -> str:
        return "gmail.search"

    @property
    def description(self) -> str:
        return "Search Gmail emails with advanced query syntax."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query (e.g., 'from:john subject:meeting after:2024/01/01')",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 20, max: 100)",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        query = kwargs.get("query", "")
        max_results = kwargs.get("max_results", 20)

        if not query:
            return SkillResult.failure("Query parameter is required")

        try:
            service = _get_gmail_service()

            results = service.users().messages().list(
                userId="me",
                q=query,
                maxResults=max_results,
            ).execute()

            messages = results.get("messages", [])
            emails = []

            for msg in messages:
                full_msg = service.users().messages().get(
                    userId="me",
                    id=msg["id"],
                    format="metadata",
                    metadataHeaders=["Subject", "From", "To", "Date"],
                ).execute()
                emails.append(_format_message(full_msg))

            return SkillResult.success({"emails": emails, "count": len(emails), "query": query})
        except Exception as e:
            logger.exception("Error searching emails")
            return SkillResult.failure(f"Error searching emails: {e}")


class GmailDraftReplySkill(SkillBase):
    """Skill to draft a reply to an email."""

    @property
    def name(self) -> str:
        return "gmail.draft_reply"

    @property
    def description(self) -> str:
        return "Create a draft reply to an email. Does not send - creates draft only."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "ID of the message to reply to",
                },
                "body": {
                    "type": "string",
                    "description": "Reply body text",
                },
            },
            "required": ["message_id", "body"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        message_id = kwargs.get("message_id", "")
        body = kwargs.get("body", "")

        if not message_id or not body:
            return SkillResult.failure("message_id and body are required")

        try:
            service = _get_gmail_service()

            # Get original message to extract headers
            original = service.users().messages().get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=["Subject", "From", "To", "Message-ID", "References"],
            ).execute()

            headers = {h["name"]: h["value"] for h in original.get("payload", {}).get("headers", [])}

            # Build reply
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            reply = MIMEMultipart()
            reply["to"] = headers.get("From", "")
            reply["subject"] = f"Re: {headers.get('Subject', '')}"
            reply["In-Reply-To"] = headers.get("Message-ID", "")
            reply["References"] = headers.get("References", "") + " " + headers.get("Message-ID", "")
            reply.attach(MIMEText(body, "plain"))

            raw = base64.urlsafe_b64encode(reply.as_bytes()).decode()

            draft = service.users().drafts().create(
                userId="me",
                body={"message": {"raw": raw}},
            ).execute()

            return SkillResult.success({
                "draft_id": draft.get("id"),
                "message_id": draft.get("message", {}).get("id"),
                "status": "draft_created",
            })
        except Exception as e:
            logger.exception("Error creating draft")
            return SkillResult.failure(f"Error creating draft: {e}")


class GmailGetThreadSkill(SkillBase):
    """Skill to get full email thread."""

    @property
    def name(self) -> str:
        return "gmail.get_thread"

    @property
    def description(self) -> str:
        return "Get full email thread with all messages."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "thread_id": {
                    "type": "string",
                    "description": "Thread ID to retrieve",
                },
            },
            "required": ["thread_id"],
        }

    async def execute(self, **kwargs: Any) -> SkillResult:
        thread_id = kwargs.get("thread_id", "")

        if not thread_id:
            return SkillResult.failure("thread_id parameter is required")

        try:
            service = _get_gmail_service()

            thread = service.users().threads().get(
                userId="me",
                id=thread_id,
                format="full",
            ).execute()

            messages = []
            for msg in thread.get("messages", []):
                messages.append(_format_message(msg, include_body=True))

            return SkillResult.success({
                "thread_id": thread_id,
                "messages": messages,
                "count": len(messages),
            })
        except Exception as e:
            logger.exception("Error getting thread")
            return SkillResult.failure(f"Error getting thread: {e}")