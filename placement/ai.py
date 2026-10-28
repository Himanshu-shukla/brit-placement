"""AI provider: Groq (hosted, fast) with local Ollama as an optional fallback."""

import json
import re
import time
import urllib.error
import urllib.request
from . import db, settings

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"


def available():
    return bool(settings.get("GROQ_API_KEY") or settings.get("OLLAMA_MODEL"))


def _groq(messages, json_mode, max_tokens, temperature):
    body = {"model": settings.get("GROQ_MODEL"), "messages": messages,
            "max_tokens": max_tokens, "temperature": temperature}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + settings.get("GROQ_API_KEY"),
               "User-Agent": "BritPlacement/2.0"}
    last = None
    for attempt in range(4):
        request = urllib.request.Request(GROQ_URL, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.load(response)
            return str(payload["choices"][0]["message"]["content"] or "").strip()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:400].decode("utf-8", "replace")
            last = f"Groq HTTP {exc.code}: {detail}"
            if exc.code in (429, 500, 502, 503):
                wait = float(exc.headers.get("retry-after") or (2 ** attempt * 2))
                time.sleep(min(wait, 20))
                continue
            raise ValueError(last) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last = f"Groq unreachable: {exc}"
            time.sleep(2)
    raise ValueError(last or "Groq request failed")


def _ollama(messages, json_mode):
    body = {"model": settings.get("OLLAMA_MODEL"), "stream": False, "messages": messages}
    if json_mode:
        body["format"] = "json"
    request = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError("Local Ollama is unavailable. Start Ollama and pull your selected model") from exc
    return str(payload.get("message", {}).get("content", "")).strip()


def chat(system, user, json_mode=False, max_tokens=1500, temperature=0.3):
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if settings.get("GROQ_API_KEY"):
        return _groq(messages, json_mode, max_tokens, temperature)
    if settings.get("OLLAMA_MODEL"):
        return _ollama(messages, json_mode)
    raise ValueError("Add your Groq API key in Settings to enable AI features")


def ask(prompt, system):
    return chat(system, prompt)


def ask_json(system, payload, max_tokens=2500, temperature=0.2):
    user = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    raw = chat(system + "\nRespond with a single valid JSON object only.", user,
               json_mode=True, max_tokens=max_tokens, temperature=temperature)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        if match:
            return json.loads(match.group(0))
        raise ValueError("AI returned invalid JSON")


def test_connection():
    key = settings.get("GROQ_API_KEY")
    if not key:
        raise ValueError("Groq API key is not set")
    request = urllib.request.Request(GROQ_MODELS_URL, headers={"Authorization": "Bearer " + key,
                                                               "User-Agent": "BritPlacement/2.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            models = sorted(m["id"] for m in json.load(response).get("data", []))
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Groq rejected the key (HTTP {exc.code})") from exc
    reply = chat("Reply with the single word OK.", "Connection test", max_tokens=5)
    return {"ok": True, "model": settings.get("GROQ_MODEL"), "reply": reply,
            "model_available": settings.get("GROQ_MODEL") in models, "models": models[:40]}


def refine(limit=10):
    limit=max(1,min(int(limit),20))
    candidates=db.rows("""SELECT m.id AS match_id,m.score,m.reasons,m.decision,l.skills,l.summary,l.projects,
      j.title,j.description FROM matches m JOIN learners l ON l.id=m.learner_id
      JOIN jobs j ON j.id=m.job_id WHERE m.decision='matched' ORDER BY m.score DESC LIMIT ?""",(limit,))
    count=0
    for candidate in candidates:
        raw=ask(json.dumps({"skills":candidate["skills"],"summary":candidate["summary"],"projects":candidate["projects"],
             "title":candidate["title"],"description":candidate["description"][:5000]}),
             "Assess alignment for entry-level UK analytics roles using only supplied data. "
             "Return ONLY JSON with score 0-100 and short evidence-based reason. "
             "Do not infer certifications, work authorization, experience or employer eligibility.")
        try:
            parsed=json.loads(raw)
            score=max(0,min(100,int(parsed["score"])))
            reason=str(parsed.get("reason", ""))[:350]
        except (json.JSONDecodeError,ValueError,KeyError,TypeError) as exc:
            raise ValueError("AI returned invalid ranking JSON; original scores unchanged") from exc
        # Preserve original rules/eligibility. AI is only a bounded ranking hint.
        new_score=max(0,min(100,max(candidate["score"]-15,min(candidate["score"]+15,score))))
        db.run("UPDATE matches SET score=?,reasons=?,matched_at=? WHERE id=?",
              (new_score,candidate["reasons"]+"; AI suggestion: "+reason,db.utc_now(),candidate["match_id"]))
        count+=1
    db.log_event("ai_refine", {"count":count})
    return {"refined":count}


def tailor_email(outreach_id):
    item=db.row("""SELECT o.*,l.name,l.skills,l.summary,l.projects,j.title,j.description,c.email
      FROM outreach o JOIN learners l ON l.id=o.learner_id JOIN jobs j ON j.id=o.job_id
      JOIN contacts c ON c.id=o.contact_id WHERE o.id=?""",(int(outreach_id),))
    if not item or item["status"]!='draft':
        raise ValueError("Only draft emails may be tailored")
    output=ask(json.dumps({"candidate_name":item["name"],"skills":item["skills"],"summary":item["summary"],
          "projects":item["projects"],"job_title":item["title"],"job_description":item["description"][:5000],
          "reference_email":item["body"]}),
          "Write one concise, professional UK recruitment email from the candidate to the recruiter. "
          "Only refer to facts explicitly given, never invent employment, degrees or achievements. "
          "End with a one-line opt-out sentence. Return only the email body.")
    if not output or len(output)>10000:
        raise ValueError("AI email content missing or excessive")
    db.run("UPDATE outreach SET body=? WHERE id=? AND status='draft'",(output,item["id"]))
    return {"id":item["id"],"body":output}
