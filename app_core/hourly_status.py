"""Local hourly report collection, independent of native widgets."""
import json
from app_core import hourly_summary, ollama, privacy
from model_advisor import detect_hardware


class HourlyStatusReport:
    def _collect(self, now, update_line, host, model, backend, server_is_local, connection_status):
        chats = []
        try:
            if self.app.chat_store is not None:
                chats = hourly_summary.recent_chats(self.app.chat_store.list_chats(), now=now)
        except Exception:
            chats = []
        snapshot = hourly_summary.work_snapshot(chats)
        reflection = hourly_summary.fallback_feedback(snapshot)
        reflected_by_model = False
        local_model = model and not model.casefold().endswith(("-cloud", ":cloud"))
        if snapshot["prompts"] and local_model and backend == "Ollama" and privacy.is_local_url(host):
            prompt = "Recent chat titles and the user's own prompts from the last hour:\n" + json.dumps(
                {"topics": snapshot["topics"], "prompts": snapshot["prompts"]}, ensure_ascii=False)
            try:
                answer = ollama.stream_chat(host, [
                    {"role": "system", "content": "Give a concise, private work check-in in at most two short sentences. Identify progress and one useful next step. Do not invent completed work."},
                    {"role": "user", "content": prompt},
                ], model, context_length=2048)
                if isinstance(answer, str) and answer.strip():
                    reflection = " ".join(answer.split())[:320]
                    reflected_by_model = True
            except Exception:
                pass
        try:
            hardware = detect_hardware(host)
        except Exception:
            hardware = {}
        if backend == "Ollama" and server_is_local:
            service_status = "Local Ollama ready" if hardware.get("ollama_running") else "Local Ollama offline"
        else:
            service_status = f"{backend} · {connection_status or 'status unavailable'}"
        self.events.put(("report", {
            "threads": snapshot["threads"],
            "topics": snapshot["topics"],
            "system": hourly_summary.hardware_summary(hardware),
            "system_attention": hourly_summary.hardware_needs_attention(hardware),
            "ollama": service_status,
            "updates": update_line,
            "reflection": reflection,
            "reflected_by_model": reflected_by_model,
            "generated_at": now,
        }))
