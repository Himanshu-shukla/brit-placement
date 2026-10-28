"""Candidate-specific answers: only explicitly approved answers can be reused."""

import re
from . import db


def normalize(question):
    return " ".join(re.findall(r"[a-z0-9]+",str(question).lower()))[:450]


def save(data):
    learner_id=int(data["learner_id"])
    learner=db.row("SELECT id FROM learners WHERE id=?",(learner_id,))
    if not learner:raise ValueError("Learner not found")
    question=str(data.get("question") or "").strip()[:700]
    answer=str(data.get("answer") or "").strip()[:2500]
    if not question or not answer:raise ValueError("Question and answer required")
    approved=data.get("approved") is True
    key=normalize(question)
    with db.connection() as conn:
        conn.execute("""INSERT INTO question_answers
         (learner_id,question,normalized_question,answer,approved,source,updated_at)
         VALUES(?,?,?,?,?,?,?) ON CONFLICT(learner_id,normalized_question) DO UPDATE SET
         question=excluded.question,answer=excluded.answer,approved=excluded.approved,
         source=excluded.source,updated_at=excluded.updated_at""",
         (learner_id,question,key,answer,int(approved),"manual",db.utc_now()))
        saved=conn.execute("SELECT * FROM question_answers WHERE learner_id=? AND normalized_question=?",(learner_id,key)).fetchone()
    return dict(saved)


def lookup(learner_id,question):
    rec=db.row("SELECT * FROM question_answers WHERE learner_id=? AND normalized_question=?",
               (int(learner_id),normalize(question)))
    if rec:
        db.run("UPDATE question_answers SET occurrence_count=occurrence_count+1 WHERE id=?",(rec["id"],))
    return rec


def suggest(data):
    # Free local LLM is optional; never silently approve inferred work-rights claims.
    from . import ai
    learner=db.row("SELECT * FROM learners WHERE id=?",(int(data["learner_id"]),))
    if not learner:raise ValueError("Learner not found")
    question=str(data.get("question") or "").strip()
    if not question:raise ValueError("Question required")
    prompt=(f"Candidate: {learner['name']}\nVerified skills: {learner['skills']}\n"
            f"Verified projects: {learner['projects']}\nUK right to work: {learner['right_to_work']}\n"
            f"Sponsorship needed: {bool(learner['sponsorship'])}\nQuestion: {question}")
    answer=ai.ask(prompt,"Draft a concise UK job-application screening answer from verified facts only. "
            "Do not guess legal status, years of work, salary history, identity or qualifications. "
            "When not enough information exists, return NEEDS_CANDIDATE_INPUT. Human approval required.")
    if not answer or answer=="NEEDS_CANDIDATE_INPUT":
        return {"question":question,"answer":"","needs_candidate_input":True}
    record=save({"learner_id":learner["id"],"question":question,"answer":answer,"approved":False})
    return {**record,"needs_candidate_input":False,"review_required":True}


# ---------------------------------------------------------------- automatic answering (Groq)

# Job-specific questions are answered fresh for each vacancy and not stored for reuse.
JOB_SPECIFIC = re.compile(r"cover letter|why (do )?you want|why are you interested|why (this|our) (company|role)|"
                          r"motivat|tell us about yourself|additional information|anything else", re.I)


def profile_facts(learner):
    keys = ("name", "email", "phone", "location", "postcode", "address", "linkedin_url", "portfolio_url", "skills",
            "summary", "projects", "experience_years", "education", "work_history", "salary_expectation",
            "notice_period", "right_to_work", "extra_facts")
    facts = {k: learner.get(k) for k in keys if learner.get(k) not in (None, "")}
    facts["needs_visa_sponsorship"] = bool(learner.get("sponsorship"))
    return facts


def _best_option(value, options):
    if not options:
        return value
    want = normalize(value)
    if not want:
        return None
    exact = next((o for o in options if normalize(o) == want), None)
    if exact is not None:
        return exact
    starts = next((o for o in options if normalize(o).startswith(want) or want.startswith(normalize(o)) and normalize(o)), None)
    if starts is not None:
        return starts
    return next((o for o in options if want in normalize(o) or normalize(o) in want and normalize(o)), None)


def _coerce(value, field):
    options = field.get("options") or []
    kind = field.get("type")
    if value is None:
        return None
    if kind == "checkbox" and options:
        values = value if isinstance(value, list) else [v.strip() for v in str(value).split("|")]
        chosen = [o for o in (_best_option(v, options) for v in values) if o is not None]
        return list(dict.fromkeys(chosen)) or None
    if isinstance(value, list):
        value = value[0] if value else ""
    value = str(value).strip()
    if not value:
        return None
    if options:
        return _best_option(value, options)
    if kind == "number":
        digits = re.search(r"-?\d+(\.\d+)?", value.replace(",", ""))
        return digits.group(0) if digits else None
    limit = int(field.get("max_length") or 0)
    return value[:limit] if limit > 0 else value


def answer_fields(learner_id, fields, job=None):
    """Answer application form fields. Saved approved answers first, then one Groq call for the rest."""
    from . import ai, settings
    learner = db.row("SELECT * FROM learners WHERE id=?", (int(learner_id),))
    if not learner:
        raise ValueError("Learner not found")
    fields = [f for f in (fields or []) if isinstance(f, dict) and f.get("key")][:60]
    answers, pending = {}, []
    for field in fields:
        label = str(field.get("label") or "")[:600]
        field["options"] = [str(o)[:200] for o in (field.get("options") or [])][:80]
        saved = None if JOB_SPECIFIC.search(label) or field.get("error") else db.row(
            "SELECT * FROM question_answers WHERE learner_id=? AND normalized_question=? AND approved=1",
            (int(learner_id), normalize(label)))
        value = _coerce(saved["answer"], field) if saved else None
        if value is not None:
            answers[field["key"]] = value
            db.run("UPDATE question_answers SET occurrence_count=occurrence_count+1 WHERE id=?", (saved["id"],))
        else:
            pending.append(field)
    if pending and ai.available():
        job = job or {}
        prompt = {
            "candidate": profile_facts(learner),
            "job": {"title": job.get("title", ""), "company": job.get("company", "") or job.get("company_name", ""),
                    "description": str(job.get("description") or "")[:3500]},
            "fields": [{"key": f["key"], "question": f.get("label", ""), "type": f.get("type", "text"),
                        "options": f["options"], "required": bool(f.get("required")),
                        **({"previous_error": str(f["error"])[:200]} if f.get("error") else {})} for f in pending],
        }
        system = (
            "You fill in UK job application forms for the candidate described. For every field return an answer "
            "in the JSON object {\"answers\": {\"<key>\": <answer>}}.\n"
            "Rules:\n"
            "- If options are given, the answer MUST be copied exactly from the options. Checkbox fields take a list.\n"
            "- Facts (employers, dates, degrees, licences, right to work, visa sponsorship, criminal record) must come "
            "from the candidate data. Right to work and sponsorship answers must match the data exactly.\n"
            "- Years of experience: use experience_years for skills the candidate lists, otherwise 0. Numbers as digits.\n"
            "- Preferences (willing to commute/relocate/work hybrid, start date, contacting references) answer positively.\n"
            "- Equal-opportunity questions (gender, ethnicity, disability, religion, orientation): choose "
            "'Prefer not to say' or the closest decline option.\n"
            "- Salary: use salary_expectation if given, else a typical UK figure for this role as a number.\n"
            "- Notice period: use notice_period if given, else 'Immediately available'.\n"
            "- Free-text motivation or cover letter fields: 80-180 words, first person, specific to the job, using only "
            "the candidate's real skills and projects.\n"
            "- How did you hear about us: 'Job board'.\n"
            "- Use null only when the answer is a personal identifier you cannot know (e.g. National Insurance number)."
        )
        try:
            result = ai.ask_json(system, prompt, max_tokens=3000)
            produced = result.get("answers", result) if isinstance(result, dict) else {}
        except ValueError as exc:
            db.log_event("ai_answer_error", {"error": str(exc)[:300]})
            produced = {}
        auto_approve = settings.enabled("AI_ANSWERS_AUTO_APPROVE")
        for field in pending:
            value = _coerce(produced.get(field["key"]), field)
            if value is None:
                continue
            answers[field["key"]] = value
            label = str(field.get("label") or "").strip()
            if label and not JOB_SPECIFIC.search(label) and len(label) > 3:
                stored = " | ".join(value) if isinstance(value, list) else str(value)
                with db.connection() as conn:
                    conn.execute("""INSERT INTO question_answers
                     (learner_id,question,normalized_question,answer,approved,source,updated_at)
                     VALUES(?,?,?,?,?,'ai',?) ON CONFLICT(learner_id,normalized_question) DO UPDATE SET
                     answer=CASE WHEN question_answers.source='ai' THEN excluded.answer ELSE question_answers.answer END,
                     updated_at=excluded.updated_at""",
                     (int(learner_id), label[:700], normalize(label), stored[:2500], int(auto_approve), db.utc_now()))
    unanswered = [f["key"] for f in fields if f["key"] not in answers]
    return {"answers": answers, "unanswered": unanswered}
