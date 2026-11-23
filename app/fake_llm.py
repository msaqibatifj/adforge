"""Offline scripted chat model so the whole graph (guard -> memory -> agent -> tools) can be tested without
an API key or spend. Enabled with ADFORGE_FAKE_LLM=1. It imitates a well-behaved model; it is NOT used in production."""
import json
import re
import uuid
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


def _text(m) -> str:
    c = m.content
    return c if isinstance(c, str) else " ".join(b.get("text", "") for b in c if isinstance(b, dict))


class FakeChat(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "fake-adforge"

    def bind_tools(self, tools, **kwargs):
        return self

    def _gen(self, msg: AIMessage, n_in: int) -> ChatResult:
        n_out = max(1, len(_text(msg)) // 4 + 10 * len(msg.tool_calls))
        msg.usage_metadata = {"input_tokens": n_in, "output_tokens": n_out, "total_tokens": n_in + n_out}
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        n_in = sum(len(_text(m)) for m in messages) // 4
        system = _text(messages[0]) if isinstance(messages[0], SystemMessage) else ""
        last = _text(messages[-1])
        if system.startswith("CLASSIFY"):
            low = last.lower()
            if "poem" in low or "capital of" in low or "python" in low or "recipe" in low or "weather" in low:
                return self._gen(AIMessage(content="OFF_TOPIC"), n_in)
            return self._gen(AIMessage(content="ON_TOPIC"), n_in)
        if system.startswith("EXTRACT"):
            facts = {}
            for key, pat in {"brand": r"(?:my|our) brand (?:is|is called|called) ([A-Za-z0-9 &'-]+?)(?:[.,;]| and |$)",
                             "audience": r"(?:my|our) (?:target )?audience is ([^.;]+)",
                             "tone": r"(?:i|we) prefer (?:a )?([a-z]+) tone",
                             "name": r"my name is ([A-Za-z]+)"}.items():
                m = re.search(pat, last, re.I)
                if m:
                    facts[key] = m.group(1).strip()
            return self._gen(AIMessage(content=json.dumps(facts)), n_in)
        return self._agent(messages, system, n_in)

    def _agent(self, messages, system, n_in):
        convo = [m for m in messages if not isinstance(m, SystemMessage)]
        last = convo[-1]
        human = next((m for m in reversed(convo) if isinstance(m, HumanMessage)), None)
        text = _text(human).lower() if human else ""
        call = lambda name, args: self._gen(AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"toolu_{uuid.uuid4().hex[:12]}"}]), n_in)

        if isinstance(last, ToolMessage):
            data = json.loads(_text(last)) if _text(last).startswith("{") else {}
            if last.name == "create_campaign" and "campaign_id" in data:
                return call("save_ad_variants", {"campaign_id": data["campaign_id"], "variants": [
                    {"headline": "Run Further Today", "body": "Lightweight comfort for every mile.", "cta": "Shop Now"},
                    {"headline": "Built For Your Pace", "body": "Cushioning that keeps up with you.", "cta": "Learn More"}]})
            if data.get("status") == "needs_confirmation":
                return self._gen(AIMessage(content=f"Please confirm: {data['summary']} Reply 'yes' to proceed."), n_in)
            if "error" in data:
                return self._gen(AIMessage(content=f"I couldn't do that: {data['error']}"), n_in)
            if last.name == "list_campaigns":
                names = ", ".join(c["name"] for c in data.get("campaigns", [])) or "none yet"
                return self._gen(AIMessage(content=f"Your campaigns: {names}."), n_in)
            return self._gen(AIMessage(content=f"Done ({last.name}). " + _text(last)[:300]), n_in)

        if "brand" in text and ("what" in text or "remember" in text):
            m = re.search(r"brand: ([^\n]+)", system)
            return self._gen(AIMessage(content=f"Your brand is {m.group(1)}." if m else "I don't have your brand yet. What is it?"), n_in)
        if "delete" in text:
            return call("delete_campaign", {"campaign_id": "latest", "confirmed": bool(re.search(r"\byes\b|confirm", text))})
        if "launch" in text or text.strip() in ("yes", "yes.", "yes, go ahead", "confirm"):
            return call("launch_campaign", {"campaign_id": "latest", "confirmed": bool(re.search(r"\byes\b|confirm|go ahead", text))})
        if "winner" in text or "analy" in text:
            return call("analyze_ab_test", {"campaign_id": "latest"})
        if "record" in text:
            nums = [int(x) for x in re.findall(r"\d+", text)]
            lab = re.search(r"variant ([a-f])", text)
            if len(nums) >= 2 and lab:
                return call("record_results", {"label": lab.group(1).upper(), "impressions": nums[0], "clicks": nums[1], "conversions": nums[2] if len(nums) > 2 else 0})
        if "list" in text and "campaign" in text:
            return call("list_campaigns", {})
        if any(w in text for w in ("create", "write", "make", "generate")) and any(w in text for w in ("ad", "campaign")):
            return call("create_campaign", {"name": "Fake Campaign", "product": "running shoes", "platform": "meta"})
        return self._gen(AIMessage(content="Happy to help with your ads. What product are we promoting?"), n_in)
