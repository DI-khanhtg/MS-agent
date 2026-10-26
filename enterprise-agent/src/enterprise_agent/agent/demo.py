"""Explicit deterministic local demonstration, without any LLM or IQ network calls."""

import re

from enterprise_agent.agent.runtime import AgentEgressBlocked, AgentResult
from enterprise_agent.artifacts.models import ArtifactSpec
from enterprise_agent.artifacts.service import ArtifactService
from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressBlockedError
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.tools.local.documents import LocalDocumentError, LocalDocumentTools
from enterprise_agent.tools.recording import (
    begin_tool_recording,
    end_tool_recording,
    get_recorded_tool_calls,
    record_tool_call,
    set_stream_result,
)


class DemoRuntime:
    def __init__(
        self, settings: Settings, documents: LocalDocumentTools, artifacts: ArtifactService
    ) -> None:
        self.settings, self.documents, self.artifacts = settings, documents, artifacts

    async def chat(
        self, message: str, session_id: str, context: RequestContext | None = None
    ) -> AgentResult:
        context = context or RequestContext(Principal.anonymous())
        identity = set_request_context(context)
        recording = begin_tool_recording(self.settings.max_tool_calls)
        try:
            safe = self.documents.gate.enforce(
                message, source="user_input", principal=context.principal
            )["data"]
            lowered = str(safe).casefold()
            if not any(
                word in lowered
                for word in (
                    "proposal",
                    "report",
                    "báo cáo",
                    "đề xuất",
                    "requirements",
                    "folder",
                    "thư mục",
                )
            ):
                return AgentResult(
                    "Chế độ demo local: chưa sử dụng LLM hay Microsoft IQ. "
                    "Hãy thử: Đọc folder Project-X và tạo proposal DOCX.",
                    [],
                )
            match = re.search(r'(?:folder|thư mục)\s*:?\s*["\']?([\w./\\-]+)', str(safe), re.I)
            folder = match.group(1) if match else "Project-X"
            sources, sections, offset, issues = [], [], 0, []
            while True:
                inventory = (await self.documents.list_local_documents(folder, offset))["data"]
                if "error" in inventory:
                    raise LocalDocumentError(inventory["error"]["message"])
                if inventory["skipped_files"] or inventory["scan_capped"]:
                    issues.append("Có file bị bỏ qua hoặc inventory bị giới hạn.")
                for file in inventory["files"]:
                    if len(get_recorded_tool_calls()) >= self.settings.max_tool_calls - 1:
                        issues.append("Đã chạm giới hạn tool calls; coverage chưa đầy đủ.")
                        break
                    chunk = (
                        await self.documents.read_local_document(document_id=file["document_id"])
                    )["data"]
                    if "error" in chunk:
                        raise LocalDocumentError(chunk["error"]["message"])
                    sources.append({"label": file["path"], "reference": chunk["source"]})
                    sections.append({"heading": file["path"], "body": chunk["text"]})
                    if chunk["next_offset"] is not None or chunk["empty"]:
                        issues.append(f"Nội dung {file['path']} chưa đọc đầy đủ hoặc cần OCR.")
                if inventory["next_offset"] is None or issues:
                    break
                offset = inventory["next_offset"]
            if not sources:
                return AgentResult(
                    "Không tìm thấy tài liệu hỗ trợ trong folder. "
                    "Chưa tạo proposal vì thiếu nguồn.",
                    get_recorded_tool_calls(),
                )
            fmt = next(
                (f for f in ("pdf", "xlsx", "pptx", "markdown", "json") if f in lowered), "docx"
            )
            if fmt == "pptx":
                slides = []
                for section in sections:
                    body = section["body"]
                    for start in range(0, max(len(body), 1), 1000):
                        slides.append(
                            {"heading": section["heading"], "body": body[start : start + 1000]}
                        )
                # Leave space for the four standard proposal sections.
                if len(slides) > 46:
                    issues.append("Slide excerpts were capped; consult the original sources.")
                sections = slides[:46]
            spec = ArtifactSpec(
                artifact_type=fmt,
                title=f"Proposal — {folder}",
                subtitle="Bản demo theo template; chưa có LLM phân tích hay dữ liệu IQ thật",
                sections=[
                    {
                        "heading": "Mục tiêu và phạm vi",
                        "body": "Bản nháp dựa trên các tài liệu dưới đây. "
                        "Cần đối chiếu requirements "
                        "và xác nhận phạm vi trước khi sử dụng.",
                    },
                    *sections,
                    {
                        "heading": "Giải pháp đề xuất",
                        "bullets": [
                            "Khảo sát requirements và dữ liệu; làm rõ các điểm mâu thuẫn.",
                            "Thiết kế giải pháp, phân quyền và tiêu chí nghiệm thu theo nguồn.",
                            "Triển khai, kiểm thử với người dùng và bàn giao vận hành.",
                        ],
                    },
                    {
                        "heading": "Timeline và chi phí",
                        "body": "Chưa lập lịch hoặc báo giá: cần xác nhận ngày bắt đầu, nhân sự, "
                        "phạm vi và ngân sách. Không suy diễn các giá trị chưa có nguồn.",
                    },
                    {
                        "heading": "Rủi ro và coverage",
                        "bullets": issues
                        or [
                            "Đã đọc các file hỗ trợ trong inventory; cần người dùng duyệt bản nháp."
                        ],
                    },
                ],
                sources=sources,
                metadata={"runtime": "demo", "coverage_complete": not issues},
            )
            record_tool_call(
                "create_artifact", {"artifact_type": fmt, "source_count": len(sources)}
            )
            record = await self.artifacts.create(spec, context.principal)
            return AgentResult(
                f"Đã tạo bản proposal demo từ {len(sources)} tài liệu: "
                f"{record.download_url}\nChưa sử dụng LLM/IQ. "
                + ("Coverage chưa đầy đủ." if issues else ""),
                get_recorded_tool_calls(),
            )
        except EgressBlockedError as exc:
            raise AgentEgressBlocked(str(exc)) from exc
        finally:
            end_tool_recording(recording)
            reset_request_context(identity)

    async def stream_chat(
        self, message: str, session_id: str, context: RequestContext | None = None
    ):
        result = await self.chat(message, session_id, context)
        set_stream_result(result.tool_calls, result.usage)
        yield result.text
