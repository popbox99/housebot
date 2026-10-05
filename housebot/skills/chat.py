"""Chat skill: everything that falls through to the LLM, with backend-tagged replies."""

from .. import llm


class Chat:
    def __init__(self, cfg):
        self.cfg = cfg

    def reply(self, text, history=None):
        raw, backend = llm.ask_llm(self.cfg, text, history=history)
        if raw is None:
            return "No LLM backend reachable - check your config's llm.backends."
        tag = f"\n\n[{backend}]" if len(self.cfg["llm"]["backends"]) > 1 else ""
        return raw + tag

    def summarize(self, text, history=None):
        prompt = ("Summarize the following in a few clear sentences.\n\n" + text[:12000])
        raw, backend = llm.ask_llm(self.cfg, prompt, history=history, temperature=0.2)
        return raw or "No LLM backend reachable."

    def extract(self, prompt):
        """Strict-extraction helper for other skills; returns raw text or None."""
        raw, _ = llm.ask_llm(self.cfg, prompt, temperature=0)
        return raw