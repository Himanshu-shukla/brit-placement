"""Generate truthful, editable candidate CVs for printing to PDF."""

import html
import io
from . import db


def resume_html(learner_id, job_id=None):
    learner = db.row("SELECT * FROM learners WHERE id=?", (int(learner_id),))
    if not learner:
        raise ValueError("Unknown learner")
    job = db.row("SELECT * FROM jobs WHERE id=?", (int(job_id),)) if job_id else None
    esc = lambda s: html.escape(str(s or ""))
    tags = "".join(f"<span>{esc(item.strip())}</span>" for item in learner["skills"].split(",") if item.strip())
    focus = f"<p class='context'>Prepared for: {esc(job['title'])}</p>" if job else ""
    # Never fabricate employers, degrees, experience, certifications or results.
    return f"""<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>
<title>CV - {esc(learner['name'])}</title><style>
@page {{size:A4;margin:19mm}}body{{font:14px/1.55 Arial,sans-serif;max-width:750px;margin:40px auto;color:#16283c}}
h1{{font-size:30px;margin-bottom:2px}}h2{{margin-top:32px;font-size:16px;border-bottom:1px solid #afc7cf;padding-bottom:5px}}
.identity{{color:#486070}}.skills span{{display:inline-block;background:#e7f4f3;margin:5px 5px 0 0;padding:4px 8px;border-radius:4px}}
.context{{color:#176c6a;font-size:12px}}.print{{position:fixed;right:20px;top:10px}}
@media print{{.print{{display:none}}body{{margin:0}}}}</style></head><body>
<button class='print' onclick='window.print()'>Save as PDF / Print</button>
<h1>{esc(learner['name'])}</h1><div class='identity'>{esc(learner['email'])} · {esc(learner['phone'])} · {esc(learner['location'])}</div>
{focus}<h2>Professional summary</h2><p>{esc(learner['summary']) or 'Summary not provided yet.'}</p>
<h2>Technical skills</h2><div class='skills'>{tags}</div>
<h2>Verified projects and achievements</h2><p style='white-space:pre-line'>{esc(learner['projects']) or 'Projects not provided yet.'}</p>
<small>Review this CV for accuracy before sending it. Include prior work and education in the verified projects field if appropriate.</small>
</body></html>"""


def resume_pdf(learner_id, job_id=None):
    """Generate a real A4 PDF if the optional free ReportLab package is installed."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    except ImportError as exc:
        raise RuntimeError("Install PDF support with: pip install reportlab") from exc
    learner = db.row("SELECT * FROM learners WHERE id=?", (int(learner_id),))
    if not learner:
        raise ValueError("Unknown learner")
    job = db.row("SELECT * FROM jobs WHERE id=?", (int(job_id),)) if job_id else None
    buffer=io.BytesIO()
    doc=SimpleDocTemplate(buffer,pagesize=A4,rightMargin=19*mm,leftMargin=19*mm,topMargin=19*mm,bottomMargin=19*mm)
    normal=ParagraphStyle('NormalCV',fontName='Helvetica',fontSize=10,leading=15,spaceAfter=9,textColor=colors.HexColor('#233044'))
    title=ParagraphStyle('TitleCV',parent=normal,fontName='Helvetica-Bold',fontSize=23,leading=28,spaceAfter=4)
    section=ParagraphStyle('SectionCV',parent=normal,fontName='Helvetica-Bold',fontSize=12,leading=19,
                           spaceBefore=13,spaceAfter=6,textColor=colors.HexColor('#137b81'))
    note=ParagraphStyle('NoteCV',parent=normal,fontSize=9,textColor=colors.HexColor('#68788c'))
    p=lambda content,style=normal:Paragraph(html.escape(str(content or '')).replace('\n','<br/>'),style)
    blocks=[p(learner['name'],title),p(' · '.join(str(learner[x]) for x in ('email','phone','location') if learner[x]),note),Spacer(1,6)]
    if job:
        blocks.append(p('Prepared for: '+job['title'],note))
    blocks.extend([HRFlowable(width='100%',thickness=1,color=colors.HexColor('#d6e4e7')),
        p('PROFESSIONAL SUMMARY',section),p(learner['summary'] or 'Summary not provided yet.'),
        p('TECHNICAL SKILLS',section),p(learner['skills'] or 'Skills not provided yet.'),
        p('VERIFIED PROJECTS & ACHIEVEMENTS',section),p(learner['projects'] or 'Projects not provided yet.')])
    doc.build(blocks)
    return buffer.getvalue()


def render_pdf(content):
    """A4 ATS-friendly PDF from structured CV content (used for per-job tailored CVs)."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    except ImportError as exc:
        raise RuntimeError("Install PDF support with: pip install reportlab") from exc
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=17*mm, leftMargin=17*mm, topMargin=15*mm, bottomMargin=15*mm,
                            title=f"{content.get('name', '')} CV", author=content.get("name", ""))
    ink = colors.HexColor('#1d2633')
    normal = ParagraphStyle('Body', fontName='Helvetica', fontSize=10, leading=14, textColor=ink, spaceAfter=4)
    name = ParagraphStyle('Name', parent=normal, fontName='Helvetica-Bold', fontSize=21, leading=25, spaceAfter=2)
    headline = ParagraphStyle('Headline', parent=normal, fontSize=11.5, textColor=colors.HexColor('#137b81'), spaceAfter=2)
    small = ParagraphStyle('Small', parent=normal, fontSize=9, textColor=colors.HexColor('#5b6878'))
    section = ParagraphStyle('Section', parent=normal, fontName='Helvetica-Bold', fontSize=11, leading=16,
                             spaceBefore=9, spaceAfter=3, textColor=colors.HexColor('#137b81'))
    role = ParagraphStyle('Role', parent=normal, fontName='Helvetica-Bold', spaceAfter=1)
    bullet = ParagraphStyle('Bullet', parent=normal, leftIndent=10, bulletIndent=0, spaceAfter=2)
    text = lambda value: html.escape(str(value or "")).replace("\n", "<br/>")
    blocks = [Paragraph(text(content.get("name")), name)]
    if content.get("headline"):
        blocks.append(Paragraph(text(content["headline"]), headline))
    blocks.append(Paragraph(text(" · ".join(x for x in content.get("contact", []) if x)), small))
    blocks.append(HRFlowable(width='100%', thickness=0.8, color=colors.HexColor('#cfdde2'), spaceBefore=4, spaceAfter=2))

    def heading(title):
        blocks.append(Paragraph(title.upper(), section))

    if content.get("summary"):
        heading("Profile")
        blocks.append(Paragraph(text(content["summary"]), normal))
    if content.get("skills"):
        heading("Key skills")
        blocks.append(Paragraph(text(" · ".join(content["skills"])), normal))
    for title, key in (("Experience", "experience"), ("Projects", "projects")):
        entries = content.get(key) or []
        if isinstance(entries, str) and entries.strip():
            heading(title)
            blocks.append(Paragraph(text(entries), normal))
            continue
        if entries:
            heading(title)
        for entry in entries:
            line = " — ".join(x for x in (entry.get("title"), entry.get("organisation")) if x)
            if entry.get("dates"):
                line += f"  <font color='#5b6878'>({html.escape(str(entry['dates']))})</font>"
            blocks.append(Paragraph(html.escape(line, quote=False).replace("&lt;font", "<font").replace("&lt;/font&gt;", "</font>").replace("'&gt;", "'>") if False else line, role))
            for point in entry.get("bullets") or []:
                blocks.append(Paragraph(text(point), bullet, bulletText="•"))
            blocks.append(Spacer(1, 3))
    education = content.get("education")
    if education:
        heading("Education")
        if isinstance(education, str):
            blocks.append(Paragraph(text(education), normal))
        else:
            for entry in education:
                blocks.append(Paragraph(text(" — ".join(x for x in (entry.get("qualification"), entry.get("institution"), entry.get("dates")) if x)), normal))
    if content.get("additional"):
        heading("Additional information")
        blocks.append(Paragraph(text(content["additional"]), normal))
    doc.build(blocks)
    return buffer.getvalue()
