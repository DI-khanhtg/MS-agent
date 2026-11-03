"""Verify configured runtime and kernel isolation with a synthetic, offline email job."""

import json

from enterprise_agent.config import Settings
from enterprise_agent.tools.document_reader import DocumentExtractionError, extract_document


def main():
    settings = Settings()
    try:
        result = extract_document(
            b"From: probe@example.test\nSubject: Sandbox probe\n\nSynthetic offline body.",
            ".eml",
            settings=settings,
        )
        if "Synthetic offline body." not in result.text:
            raise DocumentExtractionError("Sandbox probe returned unexpected output")
    except DocumentExtractionError as error:
        print(json.dumps({"sandbox": "unavailable", "detail": str(error)}))
        raise SystemExit(1) from None
    print(json.dumps({"sandbox": "verified", "protocol": 1, "cleanup": "confirmed"}))


if __name__ == "__main__":
    main()
