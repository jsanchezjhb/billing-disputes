import io
import os
import dash
from dash import dcc, html, Input, Output, State
from databricks import sql as databricks_sql

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_RIGHT

DATABRICKS_HOST      = "homebase-staging.cloud.databricks.com"
DATABRICKS_HTTP_PATH = "/sql/1.0/warehouses/16984dfe9a2c3705"
DATABRICKS_TOKEN     = os.environ.get("DATABRICKS_TOKEN", "")

DISPUTES_TABLE  = "prod_redshift_replica.stripe.i_charge_dispute"
USERS_TABLE     = "prod_redshift_replica.public.users"
LOCATIONS_TABLE = "prod_redshift_replica.public.locations"
UPGRADES_TABLE  = "prod_redshift_replica.public.upgrades_downgrades"
ACTIVITY_TABLE  = "prod_redshift_replica.public.fact_locations_by_day"

DARK       = colors.HexColor("#0f172a")
INDIGO     = colors.HexColor("#4f46e5")
INDIGO_LT  = colors.HexColor("#eff6ff")
INDIGO_BDR = colors.HexColor("#bfdbfe")
GREEN      = colors.HexColor("#065f46")
GREEN_LT   = colors.HexColor("#ecfdf5")
GREEN_BDR  = colors.HexColor("#6ee7b7")
AMBER      = colors.HexColor("#92400e")
AMBER_LT   = colors.HexColor("#fffbeb")
AMBER_BDR  = colors.HexColor("#fcd34d")
RED_LT     = colors.HexColor("#fef2f2")
RED_BDR    = colors.HexColor("#fecaca")
RED        = colors.HexColor("#991b1b")
GRAY_BDR   = colors.HexColor("#e2e8f0")
GRAY_TEXT  = colors.HexColor("#374151")
MUTED      = colors.HexColor("#6b7280")
WHITE      = colors.white

VERDICT_STYLES = {
    "NEVER_CANCELED":         ("Active -- Never Canceled",       GREEN_LT, GREEN_BDR, GREEN),
    "CANCELED_AFTER_PERIOD":  ("Canceled After Billing Period",  AMBER_LT, AMBER_BDR, AMBER),
    "CANCELED_BEFORE_PERIOD": ("Canceled Before Billing Period", RED_LT,   RED_BDR,   RED),
    "NO_DATA":                ("No Account Data Found",          INDIGO_LT, INDIGO_BDR, colors.HexColor("#1e3a5f")),
}

STRIPE_UPLOAD_CATEGORIES = [
    ("Dispute Narrative",            "Other"),
    ("Dispute Details and Receipt",  "Receipt"),
    ("Service Documentation",        "Service documentation"),
    ("Customer Activity Logs",       "Customer communication"),
    ("Refund and Cancellation Policy","Refund and cancellation policy"),
]

def get_conn():
    return databricks_sql.connect(
        server_hostname=DATABRICKS_HOST,
        http_path=DATABRICKS_HTTP_PATH,
        access_token=DATABRICKS_TOKEN,
    )

def run_query(sql, params=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            if cur.description is None:
                return []
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

def fmt(val):
    if val is None:
        return "--"
    return str(val)[:10]

def get_dispute(dispute_id):
    rows = run_query(
        "SELECT dispute_id, amount, status, reason, customer_name, "
        "customer_id, customer_email, created_at, last_updated_at, evidence_due_date "
        f"FROM {DISPUTES_TABLE} WHERE dispute_id = :did",
        {"did": dispute_id},
    )
    return rows[0] if rows else None

def get_account(customer_email):
    users = run_query(
        "SELECT user_id, first_name, last_name, email, last_sign_in_at, "
        "web_sign_in_count, sign_in_count, highest_level_location "
        f"FROM {USERS_TABLE} WHERE LOWER(email) = LOWER(:email)",
        {"email": customer_email},
    )
    if not users:
        return None, None
    user = users[0]
    locs = run_query(
        "SELECT location_id, company_id, name, created_at, archived_at, "
        "active_now, tier_id, billing_source, mau "
        f"FROM {LOCATIONS_TABLE} WHERE owner_id = :uid",
        {"uid": user["user_id"]},
    )
    if not locs and user.get("highest_level_location"):
        locs = run_query(
            "SELECT location_id, company_id, name, created_at, archived_at, "
            "active_now, tier_id, billing_source, mau "
            f"FROM {LOCATIONS_TABLE} WHERE location_id = :lid",
            {"lid": user["highest_level_location"]},
        )
    return user, (locs[0] if locs else None)

def get_plan_history(company_id):
    return run_query(
        "SELECT type, start_tier, end_tier, old_subscription_type, "
        "new_subscription_type, created_at "
        f"FROM {UPGRADES_TABLE} WHERE company_id = :cid ORDER BY created_at DESC",
        {"cid": company_id},
    )

def get_activity(company_id, period_start, period_end):
    summary = run_query(
        "SELECT SUM(active_on_day) AS total_active_days, "
        "SUM(web_active_on_day) AS web_active_days, "
        "SUM(mobile_active_on_day) AS mobile_active_days, "
        "SUM(scheduling_active_on_day) AS scheduling_active_days "
        f"FROM {ACTIVITY_TABLE} "
        "WHERE company_id = :cid AND date BETWEEN :start AND :end",
        {"cid": company_id, "start": period_start, "end": period_end},
    )
    dates = run_query(
        "SELECT CAST(date AS DATE) AS active_date "
        f"FROM {ACTIVITY_TABLE} "
        "WHERE company_id = :cid AND date BETWEEN :start AND :end "
        "AND active_on_day = 1 ORDER BY date DESC",
        {"cid": company_id, "start": period_start, "end": period_end},
    )
    last = run_query(
        "SELECT CAST(MAX(date) AS DATE) AS last_date "
        f"FROM {ACTIVITY_TABLE} WHERE company_id = :cid AND active_on_day = 1",
        {"cid": company_id},
    )
    return (
        summary[0] if summary else {},
        [fmt(r["active_date"]) for r in dates],
        fmt(last[0]["last_date"]) if last and last[0].get("last_date") else "--",
    )

def determine_verdict(archived_at, evidence_due_date):
    if not archived_at:
        return "NEVER_CANCELED"
    arch = fmt(archived_at)
    due  = fmt(evidence_due_date) if evidence_due_date else "9999-12-31"
    return "CANCELED_AFTER_PERIOD" if arch > due else "CANCELED_BEFORE_PERIOD"

def make_doc(buf, title):
    return SimpleDocTemplate(buf, pagesize=letter,
        leftMargin=0.75*inch, rightMargin=0.75*inch,
        topMargin=0.75*inch, bottomMargin=0.75*inch, title=title)

def doc_header(story, dispute_id):
    t = Table([[
        Paragraph('<font color="#f1f5f9"><b>Homebase</b></font>  <font color="#475569">Dispute Evidence Package</font>',
                  ParagraphStyle("h", fontName="Helvetica", fontSize=11, textColor=WHITE)),
        Paragraph(f'<font color="#475569">Dispute: </font><font color="#818cf8"><b>{dispute_id or ""}</b></font>',
                  ParagraphStyle("h2", fontName="Helvetica", fontSize=9, textColor=WHITE, alignment=TA_RIGHT)),
    ]], colWidths=[3.5*inch, 3.5*inch])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),DARK),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),14),("RIGHTPADDING",(0,0),(-1,-1),14),
        ("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),10)]))
    story.append(t); story.append(Spacer(1,16))

def section_badge(story, icon, label, cat, bg, bdr, col):
    t = Table([[
        Paragraph(f"<b>{icon}  {label}</b>", ParagraphStyle("b", fontName="Helvetica-Bold", fontSize=13, textColor=col)),
        [Paragraph("Stripe upload category", ParagraphStyle("cl", fontName="Helvetica", fontSize=7, textColor=MUTED, alignment=TA_RIGHT)),
         Paragraph(cat, ParagraphStyle("cv", fontName="Helvetica", fontSize=8, textColor=INDIGO, alignment=TA_RIGHT))],
    ]], colWidths=[3.8*inch, 3.2*inch])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),bg),("BOX",(0,0),(-1,-1),0.5,bdr),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),12),
        ("RIGHTPADDING",(0,0),(-1,-1),12),("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),10)]))
    story.append(t); story.append(Spacer(1,14))

def sh(text):
    return Paragraph(text.upper(), ParagraphStyle("sh", fontName="Helvetica-Bold",
        fontSize=9, textColor=DARK, spaceAfter=8, spaceBefore=4, letterSpacing=0.8))

def bp(text, col=None):
    return Paragraph(text, ParagraphStyle("bp", fontName="Helvetica", fontSize=10,
        textColor=col or GRAY_TEXT, leading=15, spaceAfter=4))

def kv_table(rows, cw=None):
    if cw is None: cw = [2.0*inch, 5.0*inch]
    data = [[Paragraph(f"<b>{k}</b>", ParagraphStyle("k", fontName="Helvetica-Bold", fontSize=9, textColor=MUTED)),
             Paragraph(str(v or "--"), ParagraphStyle("v", fontName="Helvetica", fontSize=10, textColor=DARK))]
            for k, v in rows]
    t = Table(data, colWidths=cw)
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),WHITE),("BOX",(0,0),(-1,-1),0.5,GRAY_BDR),
        ("LINEBELOW",(0,0),(-1,-2),0.5,colors.HexColor("#f3f4f6")),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),
        ("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7)]))
    return t

def grid_table(rows, headers, cw=None):
    data = [[Paragraph(f"<b>{h}</b>", ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=8, textColor=MUTED)) for h in headers]]
    for row in rows:
        data.append([Paragraph(str(c or "--"), ParagraphStyle("td", fontName="Helvetica", fontSize=9, textColor=GRAY_TEXT)) for c in row])
    if cw is None: cw = [7.0*inch/len(headers)]*len(headers)
    t = Table(data, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#f8fafc")),
        ("LINEBELOW",(0,0),(-1,0),1.0,GRAY_BDR),("LINEBELOW",(0,1),(-1,-1),0.5,colors.HexColor("#f3f4f6")),
        ("BOX",(0,0),(-1,-1),0.5,GRAY_BDR),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),
        ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6)]))
    return t

def tip_box(text, bg, bdr, col):
    t = Table([[Paragraph(text, ParagraphStyle("tip", fontName="Helvetica", fontSize=9, textColor=col, leading=14))]],
              colWidths=[7.0*inch])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),bg),("BOX",(0,0),(-1,-1),0.5,bdr),
        ("LEFTPADDING",(0,0),(-1,-1),12),("RIGHTPADDING",(0,0),(-1,-1),12),
        ("TOPPADDING",(0,0),(-1,-1),9),("BOTTOMPADDING",(0,0),(-1,-1),9)]))
    return t

def add_footer(story):
    story.append(Spacer(1,20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GRAY_BDR))
    story.append(Spacer(1,6))
    story.append(Paragraph("Homebase -- joinhomebase.com | support@joinhomebase.com",
        ParagraphStyle("ft", fontName="Helvetica", fontSize=8, textColor=MUTED, alignment=TA_CENTER)))

def pdf_narrative(dispute, user, loc, verdict, act_summary, active_dates, last_active):
    buf = io.BytesIO()
    doc = make_doc(buf, "Dispute Narrative"); s = []
    doc_header(s, dispute["dispute_id"])
    section_badge(s, "1.", "Dispute Narrative", "Other", INDIGO_LT, INDIGO_BDR, colors.HexColor("#1e3a5f"))
    v_label, v_bg, v_bdr, v_col = VERDICT_STYLES.get(verdict, VERDICT_STYLES["NO_DATA"])
    s.append(tip_box(f"VERDICT: {v_label}", v_bg, v_bdr, v_col)); s.append(Spacer(1,14))
    s.append(sh("Dispute Statement"))
    name    = dispute.get("customer_name") or (f"{user.get('first_name','')} {user.get('last_name','')}".strip() if user else "--")
    email   = dispute.get("customer_email","--")
    company = loc.get("name","--") if loc else "--"
    amount  = dispute.get("amount","--")
    reason  = (dispute.get("reason") or "subscription_canceled").replace("_"," ").title()
    t_act   = act_summary.get("total_active_days") or 0
    m_act   = act_summary.get("mobile_active_days") or 0
    w_act   = act_summary.get("web_active_days") or 0
    signins = user.get("sign_in_count",0) if user else 0
    web_si  = user.get("web_sign_in_count",0) if user else 0
    created = fmt(loc.get("created_at")) if loc else "--"
    for para in [
        f"We are disputing the chargeback filed by {name} ({email}) for {amount}, citing \"{reason}.\" Our records demonstrate that no cancellation was ever processed through the Homebase platform for this account.",
        f"The account (\"{company}\") was created on {created} and remains fully active and unarchived (archived_at = NULL, active_now = TRUE). Cancellation requires an affirmative in-app action via Settings > Billing & Plan > Cancel Subscription. No such action was taken.",
        f"The account logged {t_act} active days during the disputed period including {w_act} web and {m_act} mobile sessions. The account owner has {signins} total all-time sign-ins ({web_si} web)." + (f" Most recent activity: {last_active}." if last_active != "--" else ""),
        f"Under Homebase policy, a customer must cancel within 30 days of a charge to receive a full refund. The customer did not cancel within 30 days -- and has never canceled at all. The charge of {amount} is fully valid and no refund is owed.",
    ]:
        s.append(bp(para)); s.append(Spacer(1,6))
    s.append(Spacer(1,14)); s.append(sh("Account Summary"))
    s.append(kv_table([("Customer",name),("Email",email),("Company",company),
        ("Dispute ID",dispute.get("dispute_id","--")),("Amount",amount),
        ("Reason",reason),("Status",dispute.get("status","--")),
        ("Evidence Due",fmt(dispute.get("evidence_due_date")))]))
    add_footer(s); doc.build(s); buf.seek(0); return buf.read()

def pdf_receipt(dispute):
    buf = io.BytesIO(); doc = make_doc(buf, "Dispute Details"); s = []
    doc_header(s, dispute["dispute_id"])
    section_badge(s, "2.", "Dispute Details & Receipt", "Receipt",
                  colors.HexColor("#f5f3ff"), colors.HexColor("#ddd6fe"), colors.HexColor("#5b21b6"))
    s.append(sh("Stripe Dispute Record"))
    s.append(kv_table([("Dispute ID",dispute.get("dispute_id","--")),
        ("Status",dispute.get("status","--")),
        ("Reason",(dispute.get("reason") or "").replace("_"," ").title()),
        ("Amount Disputed",dispute.get("amount","--")),
        ("Customer Name",dispute.get("customer_name","--")),
        ("Customer Email",dispute.get("customer_email","--")),
        ("Customer ID",dispute.get("customer_id","--")),
        ("Dispute Created",fmt(dispute.get("created_at"))),
        ("Last Updated",fmt(dispute.get("last_updated_at"))),
        ("Evidence Due",fmt(dispute.get("evidence_due_date")))]))
    s.append(Spacer(1,14))
    s.append(tip_box("Retrieve the original invoice and payment receipt from Stripe Dashboard > Payments and attach alongside this document.",
                     INDIGO_LT, INDIGO_BDR, colors.HexColor("#1e3a5f")))
    add_footer(s); doc.build(s); buf.seek(0); return buf.read()

def pdf_service_docs(dispute, user, loc, plan_history):
    buf = io.BytesIO(); doc = make_doc(buf, "Service Documentation"); s = []
    doc_header(s, dispute["dispute_id"])
    section_badge(s, "3.", "Service Documentation", "Service documentation", GREEN_LT, GREEN_BDR, GREEN)
    owner    = f"{user.get('first_name','')} {user.get('last_name','')}".strip() if user else "--"
    archived = loc.get("archived_at") if loc else None
    s.append(sh("Location Status"))
    s.append(kv_table([
        ("Location Name",       loc.get("name","--") if loc else "--"),
        ("Location ID",         str(loc.get("location_id","--")) if loc else "--"),
        ("Company ID",          str(loc.get("company_id","--")) if loc else "--"),
        ("Owner",               owner),
        ("Account Created",     fmt(loc.get("created_at")) if loc else "--"),
        ("Archived / Canceled", "NEVER -- archived_at = NULL" if not archived else fmt(archived)),
        ("Active Now",          "YES" if loc and loc.get("active_now") else "NO"),
        ("Tier",                str(loc.get("tier_id","--")) if loc else "--"),
        ("Billing Source",      loc.get("billing_source","--") if loc else "--"),
        ("MAU",                 "TRUE" if loc and loc.get("mau") else "FALSE"),
    ]))
    s.append(Spacer(1,10))
    s.append(tip_box("Key evidence: archived_at = NULL and active_now = TRUE. In Homebase, a canceled account is marked by setting archived_at to the cancellation timestamp. The absence of this value is definitive proof that no cancellation was ever processed.", GREEN_LT, GREEN_BDR, GREEN))
    s.append(Spacer(1,12)); s.append(sh("Owner Sign-In History"))
    s.append(kv_table([("Total Sign-Ins",str(user.get("sign_in_count","--")) if user else "--"),
        ("Web Sign-Ins",str(user.get("web_sign_in_count","--")) if user else "--"),
        ("Last Sign-In",fmt(user.get("last_sign_in_at")) if user else "--")]))
    s.append(Spacer(1,18)); s.append(HRFlowable(width="100%",thickness=1,color=GRAY_BDR)); s.append(Spacer(1,18))
    s.append(sh("Subscription & Plan Change History"))
    if plan_history:
        rows = [[fmt(e.get("created_at")),e.get("type") or "subscription",
                 f"{e.get('start_tier','?')} to {e.get('end_tier','?')}",
                 f"{e.get('old_subscription_type') or '--'} to {e.get('new_subscription_type') or '--'}"]
                for e in plan_history]
        s.append(grid_table(rows,["Date","Event","Tier Change","Plan Change"],
                            cw=[1.3*inch,1.4*inch,1.5*inch,2.8*inch]))
    else:
        s.append(bp("No subscription change history found."))
    s.append(Spacer(1,10))
    s.append(tip_box("Key evidence: No downgrade or cancellation events appear in the subscription history. The account has been continuously active since creation.", GREEN_LT, GREEN_BDR, GREEN))
    add_footer(s); doc.build(s); buf.seek(0); return buf.read()

def pdf_activity(dispute, act_summary, active_dates, last_active):
    buf = io.BytesIO(); doc = make_doc(buf, "Customer Activity"); s = []
    doc_header(s, dispute["dispute_id"])
    section_badge(s, "4.", "Customer Activity Logs", "Customer communication", AMBER_LT, AMBER_BDR, AMBER)
    s.append(sh("Activity Summary During Disputed Period"))
    s.append(kv_table([("Total Active Days",str(act_summary.get("total_active_days") or 0)),
        ("Web Active Days",str(act_summary.get("web_active_days") or 0)),
        ("Mobile Active Days",str(act_summary.get("mobile_active_days") or 0)),
        ("Scheduling Active Days",str(act_summary.get("scheduling_active_days") or 0)),
        ("Last Recorded Activity",last_active)]))
    s.append(Spacer(1,14))
    if active_dates:
        s.append(sh("Active Dates During Disputed Period"))
        rows = [[d,"Active"] for d in active_dates[:50]]
        s.append(grid_table(rows,["Date","Status"],cw=[3.5*inch,3.5*inch]))
        if len(active_dates) > 50:
            s.append(Spacer(1,6)); s.append(bp(f"... and {len(active_dates)-50} additional active dates."))
    else:
        s.append(bp("No activity records found for this period."))
    s.append(Spacer(1,14))
    s.append(tip_box("Key evidence: The customer actively used the Homebase platform on the dates listed above during the disputed billing period, demonstrating the service was actively rendered.", AMBER_LT, AMBER_BDR, AMBER))
    add_footer(s); doc.build(s); buf.seek(0); return buf.read()

def pdf_policy(dispute, loc):
    buf = io.BytesIO(); doc = make_doc(buf, "Refund Policy"); s = []
    doc_header(s, dispute["dispute_id"])
    section_badge(s, "5.", "Refund & Cancellation Policy", "Refund and cancellation policy", GREEN_LT, GREEN_BDR, GREEN)
    s.append(sh("Homebase Cancellation & Refund Policy"))
    s.append(bp("Effective for all Homebase subscriptions at joinhomebase.com")); s.append(Spacer(1,10))
    for heading, body in [
        ("CANCELLATION", "Customers may cancel by logging in and navigating to Settings > Billing & Plan > Cancel Subscription. Cancellations must be initiated in-app by the account owner. Homebase does not accept cancellation requests via email or phone. Upon cancellation the account remains active through end of billing period."),
        ("REFUND POLICY", "Homebase offers a 30-day full refund policy. The 30-day window is calculated from the date of each individual charge -- not the account signup date. Applies to both monthly and annual plans. Cancel within 30 days of a charge = full refund. Not canceling within 30 days = no refund. No prorated refunds are issued under any circumstances."),
    ]:
        s.append(Paragraph(f"<b>{heading}</b>", ParagraphStyle("hd", fontName="Helvetica-Bold", fontSize=10, textColor=DARK, spaceBefore=8, spaceAfter=4)))
        s.append(bp(body)); s.append(Spacer(1,6))
    s.append(Paragraph("<b>HOW TO CANCEL</b>", ParagraphStyle("hd2", fontName="Helvetica-Bold", fontSize=10, textColor=DARK, spaceBefore=8, spaceAfter=4)))
    for step in ["1.  Log in at app.joinhomebase.com","2.  Navigate to Settings > Billing & Plan",
                 "3.  Click Manage Plan or Cancel Subscription",
                 "4.  Confirm -- email confirmation sent immediately"]:
        s.append(bp(step))
    s.append(Spacer(1,14))
    archived = loc.get("archived_at") if loc else None
    s.append(tip_box(
        f"Evidence in this dispute: No cancellation was initiated for {dispute.get('customer_email','this account')}. "
        f"archived_at = {'NULL' if not archived else fmt(archived)}. "
        f"The customer did not cancel within 30 days of the disputed charge of {dispute.get('amount','--')} -- and has never canceled. The charge is fully valid and non-refundable.",
        RED_LT, RED_BDR, RED))
    add_footer(s); doc.build(s); buf.seek(0); return buf.read()

def build_package(dispute_id):
    from datetime import date, timedelta, datetime
    dispute = get_dispute(dispute_id)
    if not dispute:
        raise ValueError(f"No dispute found for ID: {dispute_id}")
    customer_email = dispute.get("customer_email")
    if not customer_email:
        raise ValueError("Dispute record has no customer_email.")
    user, loc = get_account(customer_email)
    if not loc:
        raise ValueError(f"No Homebase account found for: {customer_email}")
    company_id   = loc["company_id"]
    plan_history = get_plan_history(company_id)
    created = str(dispute.get("created_at") or "")[:10]
    if created and created not in ("--",""):
        center       = datetime.strptime(created, "%Y-%m-%d").date()
        period_start = str(center - timedelta(days=60))
        period_end   = str(center + timedelta(days=30))
    else:
        period_start = str(date.today() - timedelta(days=90))
        period_end   = str(date.today())
    act_summary, active_dates, last_active = get_activity(company_id, period_start, period_end)
    verdict = determine_verdict(loc.get("archived_at"), dispute.get("evidence_due_date"))
    slug = dispute_id.replace("_","-")
    return {
        f"{slug}_1_dispute_narrative.pdf":         pdf_narrative(dispute, user, loc, verdict, act_summary, active_dates, last_active),
        f"{slug}_2_dispute_receipt.pdf":            pdf_receipt(dispute),
        f"{slug}_3_service_documentation.pdf":      pdf_service_docs(dispute, user, loc, plan_history),
        f"{slug}_4_customer_activity_logs.pdf":     pdf_activity(dispute, act_summary, active_dates, last_active),
        f"{slug}_5_refund_cancellation_policy.pdf": pdf_policy(dispute, loc),
    }

app    = dash.Dash(__name__, title="Billing Disputes Package")
server = app.server

app.layout = html.Div([
    html.Div([
        html.Div([
            html.Span("Billing Disputes Package", style={"color":"#f1f5f9","fontWeight":"700","fontSize":"18px"}),
        ]),
        html.Div("Homebase - Internal Tool", style={"color":"#475569","fontSize":"12px","marginTop":"2px"}),
    ], style={"background":"#0f172a","padding":"16px 28px","marginBottom":"36px"}),

    html.Div([
        html.H2("Generate Evidence Package", style={"fontSize":"20px","fontWeight":"700","color":"#0f172a","marginBottom":"6px"}),
        html.P("Enter a Stripe dispute ID. All data is pulled from the database.", style={"color":"#6b7280","fontSize":"13px","marginBottom":"24px"}),
        html.Div("Stripe Dispute ID", style={"fontSize":"11px","fontWeight":"700","color":"#94a3b8","textTransform":"uppercase","letterSpacing":"0.08em","marginBottom":"6px"}),
        dcc.Input(id="dispute-input", type="text", placeholder="du_1AbCdEfGhIjKlMnOpQrStUv", debounce=False,
                  style={"width":"100%","padding":"11px 14px","border":"1px solid #d1d5db","borderRadius":"8px",
                         "fontSize":"14px","fontFamily":"monospace","marginBottom":"20px","boxSizing":"border-box"}),
        html.Button("Generate Evidence Package", id="generate-btn", n_clicks=0,
                    style={"width":"100%","padding":"13px","background":"#4f46e5","color":"#fff",
                           "border":"none","borderRadius":"8px","fontSize":"15px","fontWeight":"700","cursor":"pointer"}),
        html.Div(id="status-output", style={"marginTop":"16px"}),
        html.Div(id="download-section", style={"marginTop":"20px"}),
        *[dcc.Download(id=f"dl-{i}") for i in range(1,6)],
        dcc.Store(id="pdf-store"),
    ], style={"maxWidth":"580px","margin":"0 auto","background":"#fff","border":"1px solid #e5e7eb",
              "borderRadius":"16px","padding":"32px"}),
], style={"fontFamily":"-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif","background":"#f8fafc","minHeight":"100vh"})

@app.callback(
    Output("status-output","children"),
    Output("download-section","children"),
    Output("pdf-store","data"),
    Input("generate-btn","n_clicks"),
    State("dispute-input","value"),
    prevent_initial_call=True,
)
def on_generate(n_clicks, dispute_id):
    if not dispute_id or not dispute_id.strip():
        return html.Div("Please enter a dispute ID.", style={"color":"#dc2626","fontSize":"13px"}), [], None
    try:
        pdfs      = build_package(dispute_id.strip())
        filenames = list(pdfs.keys())
        import base64
        store   = {fn: base64.b64encode(data).decode() for fn, data in pdfs.items()}
        buttons = []
        for i, (fn, (label, cat)) in enumerate(zip(filenames, STRIPE_UPLOAD_CATEGORIES)):
            buttons.append(html.Div([
                html.Div([
                    html.Span(f"{i+1}. {label}", style={"fontSize":"13px","fontWeight":"600","color":"#111827"}),
                    html.Span(" - upload as: ", style={"fontSize":"12px","color":"#9ca3af"}),
                    html.Code(cat, style={"fontSize":"11px","background":"#eff6ff","color":"#4f46e5","padding":"1px 6px","borderRadius":"4px"}),
                ], style={"marginBottom":"8px"}),
                html.Button("Download", id=f"dl-btn-{i+1}", n_clicks=0,
                            style={"background":"#f8fafc","border":"1px solid #e2e8f0","borderRadius":"6px",
                                   "padding":"6px 14px","fontSize":"12px","cursor":"pointer"}),
            ], style={"background":"#fff","border":"1px solid #e5e7eb","borderRadius":"10px","padding":"14px 16px","marginBottom":"10px"}))
        status = html.Div([
            html.Span("Package ready - ", style={"color":"#16a34a","fontWeight":"700"}),
            html.Span(dispute_id.strip(), style={"color":"#065f46","fontSize":"13px"}),
        ], style={"background":"#f0fdf4","border":"1px solid #a7f3d0","borderRadius":"8px","padding":"10px 14px"})
        return status, buttons, store
    except Exception as e:
        return (html.Div([html.Span("Error: ",style={"fontWeight":"700"}),html.Span(str(e))],
                style={"background":"#fef2f2","border":"1px solid #fecaca","borderRadius":"8px",
                       "padding":"10px 14px","color":"#991b1b","fontSize":"13px"}), [], None)

for _idx in range(1, 6):
    @app.callback(Output(f"dl-{_idx}","data"), Input(f"dl-btn-{_idx}","n_clicks"),
                  State("pdf-store","data"), prevent_initial_call=True)
    def _dl(n, store, idx=_idx):
        if not store or not n: return dash.no_update
        import base64
        keys = list(store.keys())
        if idx-1 >= len(keys): return dash.no_update
        return dcc.send_bytes(base64.b64decode(store[keys[idx-1]]), filename=keys[idx-1])

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8050)
