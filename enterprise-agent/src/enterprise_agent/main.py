"""Console entry point."""

import uvicorn

from enterprise_agent.config import get_settings


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "enterprise_agent.api.app:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_env == "development",
    )


if __name__ == "__main__":
    run()

