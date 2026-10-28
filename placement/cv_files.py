"""Immutable candidate CV variants and lightweight evidence-backed CV checks."""

import base64
import io
import os
import re
import secrets
import zipfile
from html import escape
from pathlib import Path
from xml.etree import ElementTree

from . import config, db, cv, matching

MAX_FILE_BYTES=6_000_000


def _persist(learner_id, label, filename, mime, binary, make_default=False):
    learner=db.row("SELECT * FROM learners WHERE id=?",(int(learner_id),))
    if not learner:
        raise ValueError("Learner not found")
    if len(binary)>MAX_FILE_BYTES or len(binary)<20:
        raise ValueError("CV must be between 20 bytes and 6 MB")
    suffix=".pdf" if mime=="application/pdf" else ".docx"
    if mime=="application/pdf" and not binary.startswith(b"%PDF-"):
        raise ValueError("Invalid PDF signature")
    if mime=="application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        if not zipfile.is_zipfile(io.BytesIO(binary)):
            raise ValueError("Invalid DOCX file")
        with zipfile.ZipFile(io.BytesIO(binary)) as archive:
            if "word/document.xml" not in archive.namelist():
                raise ValueError("DOCX missing Word document")
            if archive.getinfo("word/document.xml").file_size>5_000_000:
                raise ValueError("DOCX document XML is unreasonably large")
    elif mime!="application/pdf":
        raise ValueError("Only PDF and DOCX CV files are supported")
    config.bootstrap_directories()
    target=config.CV_DIR/(secrets.token_hex(15)+suffix)
    with open(target,"xb") as stream:
        stream.write(binary)
    try:
        os.chmod(target,0o600)
    except OSError:
        pass
    with db.connection() as conn:
        if make_default:
            conn.execute("UPDATE cv_versions SET is_default=0 WHERE learner_id=?",(int(learner_id),))
        cursor=conn.execute("""INSERT INTO cv_versions
          (learner_id,label,filename,mime_type,path,is_default,created_at)
          VALUES(?,?,?,?,?,?,?)""",
          (int(learner_id),str(label or "Master CV")[:100],Path(filename).name[:150],mime,
           str(target),int(bool(make_default)),db.utc_now()))
        if make_default:
            conn.execute("UPDATE learners SET cv_path=? WHERE id=?",(str(target),int(learner_id)))
        cv_id=cursor.lastrowid
    return db.row("SELECT id,learner_id,label,filename,mime_type,is_default,created_at FROM cv_versions WHERE id=?",(cv_id,))


def upload(data):
    encoded=str(data.get("content_base64") or "")
    if len(encoded)>8_100_000:
        raise ValueError("CV upload too large")
    try:
        binary=base64.b64decode(encoded,validate=True)
    except (ValueError,base64.binascii.Error) as exc:
        raise ValueError("Invalid base64 file content") from exc
    filename=Path(str(data.get("filename") or "cv.pdf")).name
    mime="application/pdf" if filename.lower().endswith(".pdf") else "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if filename.lower().endswith(".docx") else ""
    return _persist(int(data["learner_id"]),data.get("label",filename),filename,mime,binary,data.get("is_default",False))


def generate(learner_id,label="Generated master CV",job_id=None):
    pdf=cv.resume_pdf(int(learner_id),int(job_id) if job_id else None)
    return _persist(learner_id,label,"generated-cv.pdf","application/pdf",pdf,True)


def read_variant(cv_id,learner_id=None):
    info=db.row("SELECT * FROM cv_versions WHERE id=?",(int(cv_id),))
    if not info or learner_id is not None and info["learner_id"]!=int(learner_id):
        raise ValueError("CV version not found for learner")
    path=Path(info["path"])
    if not path.is_file() or config.CV_DIR.resolve() not in path.resolve().parents:
        raise ValueError("CV file no longer exists in private storage")
    return info,path.read_bytes()


def extract_text(info,binary):
    if info["mime_type"]=="application/pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("To analyse uploaded PDFs: pip install pypdf") from exc
        pdf=PdfReader(io.BytesIO(binary))
        if len(pdf.pages)>15:
            raise ValueError("CV exceeds 15 pages")
        return "\n".join(page.extract_text() or "" for page in pdf.pages)[:50000]
    with zipfile.ZipFile(io.BytesIO(binary)) as archive:
        if archive.getinfo("word/document.xml").file_size>5_000_000:
            raise ValueError("DOCX document XML is unreasonably large")
        xml=archive.read("word/document.xml")
    root=ElementTree.fromstring(xml)
    return " ".join(element.text or "" for element in root.iter() if element.tag.endswith("}t"))[:50000]


def check(cv_id,job_id=None):
    info,binary=read_variant(cv_id)
    text=extract_text(info,binary)
    lower=text.lower()
    strengths=[]
    warnings=[]
    score=25
    if re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}",lower):
        score+=15;strengths.append("Contact email present")
    else:warnings.append("Add a professional email address")
    if 200<=len(text)<=12000:
        score+=15;strengths.append("CV has substantial readable text")
    else:warnings.append("CV text is very short, long or scanned as an image")
    for label,regex in (("skills",r"\b(skills|technologies|technical)\b"),
                        ("experience",r"\b(experience|employment|professional)\b"),
                        ("projects",r"\b(projects?|portfolio|case study)\b")):
        if re.search(regex,lower):
            score+=8;strengths.append(f"{label.title()} section detected")
        else:warnings.append(f"Consider a clearly labelled {label} section")
    if re.search(r"\b\d+(%|\s+(?:hours|days|reports|users|records))\b",lower):
        score+=8;strengths.append("Quantified accomplishments detected")
    else:warnings.append("Where truthful, quantify verified project outcomes")
    if job_id:
        job=db.row("SELECT * FROM jobs WHERE id=?",(int(job_id),))
        if job:
            job_skills=matching.skills_in(job.get("description","")+" "+job["title"])
            cv_skills=matching.skills_in(text)
            overlap=sorted(job_skills & cv_skills)
            missing=sorted(job_skills-cv_skills)
            if overlap:
                score+=min(12,len(overlap)*3)
                strengths.append("Job skills evidenced: "+", ".join(overlap))
            if missing:
                warnings.append("Job mentions skills not visible in CV: "+", ".join(missing))
    return {"cv_id":cv_id,"score":min(score,100),"strengths":strengths,"warnings":warnings,
            "characters_read":len(text),"job_id":job_id,"automated_check":True}
