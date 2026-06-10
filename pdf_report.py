"""
pdf_report.py — Monthly Financial Report Generator
Uses reportlab to create a clean PDF report
"""
try:
    from reportlab.lib.pagesizes import A4
except Exception:
    # Fallback A4 size in points (width, height) if reportlab not installed or unresolved by linter
    A4 = (595.2755905511812, 841.8897637795277)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, HRFlowable, KeepTogether)
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.graphics import renderPDF
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from collections import defaultdict
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
import io

# ── COLOUR PALETTE ──
C_ACCENT   = colors.HexColor('#2563eb')
C_GREEN    = colors.HexColor('#16a34a')
C_RED      = colors.HexColor('#dc2626')
C_ORANGE   = colors.HexColor('#d97706')
C_BG       = colors.HexColor('#f7f7f4')
C_SURFACE  = colors.HexColor('#ffffff')
C_TEXT     = colors.HexColor('#1c1c1a')
C_TEXT2    = colors.HexColor('#6b6b63')
C_TEXT3    = colors.HexColor('#a0a098')
C_BORDER   = colors.HexColor('#e5e5e0')
C_ACCENT_L = colors.HexColor('#dbeafe')

W, H = A4


def build_styles():
    base = getSampleStyleSheet()
    return {
        'title':    ParagraphStyle('title',    fontName='Helvetica-Bold',   fontSize=22, textColor=C_TEXT,  leading=28, spaceAfter=4),
        'subtitle': ParagraphStyle('subtitle', fontName='Helvetica',        fontSize=11, textColor=C_TEXT2, leading=16, spaceAfter=2),
        'h2':       ParagraphStyle('h2',       fontName='Helvetica-Bold',   fontSize=13, textColor=C_TEXT,  leading=18, spaceBefore=14, spaceAfter=6),
        'h3':       ParagraphStyle('h3',       fontName='Helvetica-Bold',   fontSize=10, textColor=C_TEXT2, leading=14, spaceBefore=8,  spaceAfter=4),
        'body':     ParagraphStyle('body',     fontName='Helvetica',        fontSize=9,  textColor=C_TEXT,  leading=14),
        'small':    ParagraphStyle('small',    fontName='Helvetica',        fontSize=8,  textColor=C_TEXT2, leading=12),
        'mono':     ParagraphStyle('mono',     fontName='Courier',          fontSize=9,  textColor=C_TEXT,  leading=14),
        'label':    ParagraphStyle('label',    fontName='Helvetica-Bold',   fontSize=7,  textColor=C_TEXT2, leading=10, spaceAfter=2),
        'green':    ParagraphStyle('green',    fontName='Helvetica-Bold',   fontSize=11, textColor=C_GREEN, leading=16),
        'red':      ParagraphStyle('red',      fontName='Helvetica-Bold',   fontSize=11, textColor=C_RED,   leading=16),
        'accent':   ParagraphStyle('accent',   fontName='Helvetica-Bold',   fontSize=11, textColor=C_ACCENT,leading=16),
        'center':   ParagraphStyle('center',   fontName='Helvetica',        fontSize=9,  textColor=C_TEXT2, leading=14, alignment=TA_CENTER),
        'footer':   ParagraphStyle('footer',   fontName='Helvetica',        fontSize=7,  textColor=C_TEXT3, leading=10, alignment=TA_CENTER),
    }


def fmt(amount, symbol='LKR'):
    return f"{symbol} {amount:,.0f}"


def divider():
    return HRFlowable(width='100%', thickness=0.5, color=C_BORDER, spaceAfter=10, spaceBefore=4)


def section_header(text, styles):
    return [
        Paragraph(text, styles['h2']),
        HRFlowable(width='100%', thickness=1, color=C_ACCENT, spaceAfter=8),
    ]


def stat_table(rows, styles, symbol='LKR'):
    """2-column stat rows: label | value"""
    data = []
    for label, value, color in rows:
        c = {'green': C_GREEN, 'red': C_RED, 'orange': C_ORANGE, 'accent': C_ACCENT}.get(color, C_TEXT)
        data.append([
            Paragraph(label, styles['small']),
            Paragraph(f'<b>{value}</b>', ParagraphStyle('v', fontName='Helvetica-Bold', fontSize=10, textColor=c, leading=14, alignment=TA_RIGHT)),
        ])
    t = Table(data, colWidths=[110*mm, 60*mm])
    t.setStyle(TableStyle([
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [C_SURFACE, C_BG]),
        ('TOPPADDING',  (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING',  (0,0), (0,-1), 8),
        ('RIGHTPADDING', (1,0), (1,-1), 8),
        ('LINEBELOW', (0,-1), (-1,-1), 0.5, C_BORDER),
    ]))
    return t


def mini_bar_chart(category_data, max_width=150, height=12):
    """Simple horizontal bar chart as a table"""
    if not category_data:
        return Spacer(1, 4)
    max_val = max(v for _, v in category_data) or 1
    rows = []
    for cat, val in category_data[:8]:
        bar_w = int((val / max_val) * max_width)
        d = Drawing(max_width, height)
        d.add(Rect(0, 2, bar_w, height-4, fillColor=C_ACCENT, strokeColor=None))
        rows.append([
            Paragraph(cat[:28], ParagraphStyle('bc', fontName='Helvetica', fontSize=8, textColor=C_TEXT, leading=10)),
            d,
            Paragraph(f'{val:,.0f}', ParagraphStyle('bv', fontName='Courier', fontSize=8, textColor=C_TEXT2, leading=10, alignment=TA_RIGHT)),
        ])
    t = Table(rows, colWidths=[55*mm, max_width*0.72, 25*mm])
    t.setStyle(TableStyle([
        ('TOPPADDING',    (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING',   (0,0), (-1,-1), 0),
        ('RIGHTPADDING',  (0,0), (-1,-1), 0),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
    ]))
    return t


def progress_bar_row(label, current, target, color=C_ACCENT, width=100):
    pct = min(100, current / target * 100) if target > 0 else 0
    bar_w = int(pct / 100 * width)
    d = Drawing(width, 10)
    d.add(Rect(0, 2, width, 6, fillColor=C_BG, strokeColor=C_BORDER, strokeWidth=0.5))
    if bar_w > 0:
        d.add(Rect(0, 2, bar_w, 6, fillColor=color, strokeColor=None))
    return [
        Paragraph(label, ParagraphStyle('pl', fontName='Helvetica', fontSize=8, textColor=C_TEXT, leading=10)),
        d,
        Paragraph(f'{pct:.0f}%', ParagraphStyle('pp', fontName='Courier', fontSize=8, textColor=C_TEXT2, leading=10, alignment=TA_RIGHT)),
    ]


def generate_monthly_report(user_id, user_name, user_email, currency_symbol,
                              month_date, transactions, wallets, loans, cards,
                              goals, fixed_expenses, health, settings):
    """Generate PDF and return bytes."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=18*mm, bottomMargin=18*mm,
        title=f"FinanceOS Report — {month_date.strftime('%B %Y')}",
        author=user_name,
    )
    S = build_styles()
    story = []

    # ── COVER ──
    story.append(Spacer(1, 10*mm))

    # Logo bar
    logo_data = [[
        Paragraph('<b>💰 FinanceOS</b>', ParagraphStyle('logo', fontName='Helvetica-Bold', fontSize=16, textColor=C_ACCENT, leading=20)),
        Paragraph(f'Monthly Report', ParagraphStyle('lr', fontName='Helvetica', fontSize=10, textColor=C_TEXT2, leading=14, alignment=TA_RIGHT)),
    ]]
    logo_t = Table(logo_data, colWidths=[90*mm, 84*mm])
    logo_t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'BOTTOM'), ('BOTTOMPADDING',(0,0),(-1,-1),8)]))
    story.append(logo_t)
    story.append(HRFlowable(width='100%', thickness=2, color=C_ACCENT, spaceAfter=12))

    story.append(Paragraph(month_date.strftime('%B %Y'), S['title']))
    story.append(Paragraph(f'Prepared for: <b>{user_name}</b> ({user_email})', S['subtitle']))
    story.append(Paragraph(f'Generated on: {date.today().strftime("%d %B %Y")}', S['subtitle']))
    story.append(Spacer(1, 8*mm))

    # ── PERIOD SUMMARY ──
    story += section_header('📊 Financial Summary', S)

    total_income = sum(t.amount for t in transactions if t.trans_type == 'income')
    total_expense = sum(t.amount for t in transactions if t.trans_type == 'expense')
    net = total_income - total_expense
    savings_rate = (net / total_income * 100) if total_income > 0 else 0
    txn_count = len(transactions)

    # Summary cards in 2x3 grid
    summary_data = [
        [
            _stat_cell('Total Income', fmt(total_income, currency_symbol), C_GREEN),
            _stat_cell('Total Expenses', fmt(total_expense, currency_symbol), C_RED),
            _stat_cell('Net Balance', fmt(net, currency_symbol), C_ACCENT if net >= 0 else C_RED),
        ],
        [
            _stat_cell('Savings Rate', f'{savings_rate:.1f}%', C_GREEN if savings_rate > 20 else C_ORANGE),
            _stat_cell('Transactions', str(txn_count), C_TEXT),
            _stat_cell('Health Score', f'{health["score"]}/100 {health["label"]}',
                       colors.HexColor(health['color'])),
        ],
    ]
    summary_t = Table(summary_data, colWidths=[58*mm, 58*mm, 58*mm])
    summary_t.setStyle(TableStyle([
        ('TOPPADDING',    (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING',   (0,0), (-1,-1), 4),
        ('RIGHTPADDING',  (0,0), (-1,-1), 4),
    ]))
    story.append(summary_t)
    story.append(Spacer(1, 4*mm))

    # ── SPENDING BY CATEGORY ──
    story += section_header('🛍️ Spending by Category', S)
    by_cat = defaultdict(float)
    for t in transactions:
        if t.trans_type == 'expense':
            by_cat[t.category or 'Other'] += t.amount
    sorted_cats = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)

    if sorted_cats:
        story.append(mini_bar_chart(sorted_cats))
        story.append(Spacer(1, 4*mm))

        # Category table
        cat_data = [['Category', 'Amount', '% of Expenses']]
        for cat, amt in sorted_cats[:10]:
            pct_val = amt / total_expense * 100 if total_expense > 0 else 0
            cat_data.append([cat, fmt(amt, currency_symbol), f'{pct_val:.1f}%'])

        cat_t = Table(cat_data, colWidths=[80*mm, 50*mm, 44*mm])
        cat_t.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (-1,0),  C_ACCENT),
            ('TEXTCOLOR',     (0,0), (-1,0),  colors.white),
            ('FONTNAME',      (0,0), (-1,0),  'Helvetica-Bold'),
            ('FONTSIZE',      (0,0), (-1,-1), 8),
            ('ROWBACKGROUNDS',(0,1), (-1,-1), [C_SURFACE, C_BG]),
            ('TOPPADDING',    (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('LEFTPADDING',   (0,0), (-1,-1), 8),
            ('GRID',          (0,0), (-1,-1), 0.25, C_BORDER),
            ('ALIGN',         (1,0), (-1,-1), 'RIGHT'),
        ]))
        story.append(cat_t)
    else:
        story.append(Paragraph('No expenses recorded this period.', S['small']))

    story.append(Spacer(1, 4*mm))

    # ── TOP TRANSACTIONS ──
    story += section_header('📋 Top Transactions', S)
    top_exp = sorted([t for t in transactions if t.trans_type == 'expense'],
                     key=lambda x: x.amount, reverse=True)[:8]
    if top_exp:
        txn_data = [['Date', 'Description', 'Category', 'Amount']]
        for t in top_exp:
            d = t.date.strftime('%d %b') if isinstance(t.date, (date, datetime)) else str(t.date)[:10]
            txn_data.append([d, (t.description or '')[:35], (t.category or 'Other')[:20], fmt(t.amount, currency_symbol)])
        txn_t = Table(txn_data, colWidths=[20*mm, 70*mm, 40*mm, 44*mm])
        txn_t.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (-1,0),  C_ACCENT),
            ('TEXTCOLOR',     (0,0), (-1,0),  colors.white),
            ('FONTNAME',      (0,0), (-1,0),  'Helvetica-Bold'),
            ('FONTSIZE',      (0,0), (-1,-1), 8),
            ('ROWBACKGROUNDS',(0,1), (-1,-1), [C_SURFACE, C_BG]),
            ('TOPPADDING',    (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('LEFTPADDING',   (0,0), (-1,-1), 8),
            ('GRID',          (0,0), (-1,-1), 0.25, C_BORDER),
            ('ALIGN',         (3,0), (3,-1),  'RIGHT'),
        ]))
        story.append(txn_t)
    else:
        story.append(Paragraph('No transactions this period.', S['small']))

    story.append(Spacer(1, 4*mm))

    # ── WALLETS ──
    story += section_header('💼 Wallet Balances', S)
    if wallets:
        w_data = [['Wallet', 'Type', 'Balance']]
        total_w = sum(w.balance for w in wallets)
        for w in wallets:
            w_data.append([w.name, w.wallet_type or '—', fmt(w.balance, currency_symbol)])
        w_data.append(['TOTAL', '', fmt(total_w, currency_symbol)])
        w_t = Table(w_data, colWidths=[80*mm, 50*mm, 44*mm])
        w_t.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (-1,0),  C_ACCENT),
            ('TEXTCOLOR',     (0,0), (-1,0),  colors.white),
            ('FONTNAME',      (0,0), (-1,0),  'Helvetica-Bold'),
            ('FONTNAME',      (0,-1), (-1,-1), 'Helvetica-Bold'),
            ('BACKGROUND',    (0,-1), (-1,-1), C_ACCENT_L),
            ('FONTSIZE',      (0,0), (-1,-1), 8),
            ('ROWBACKGROUNDS',(0,1), (-1,-2), [C_SURFACE, C_BG]),
            ('TOPPADDING',    (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('LEFTPADDING',   (0,0), (-1,-1), 8),
            ('GRID',          (0,0), (-1,-1), 0.25, C_BORDER),
            ('ALIGN',         (2,0), (2,-1),  'RIGHT'),
        ]))
        story.append(w_t)

    story.append(Spacer(1, 4*mm))

    # ── LOANS & CREDIT CARDS ──
    if loans or cards:
        story += section_header('🏦 Liabilities', S)
        liab_data = [['Name', 'Type', 'Outstanding', 'Monthly']]
        for l in loans:
            liab_data.append([l.loan_name, 'Loan', fmt(l.outstanding_balance, currency_symbol), fmt(l.monthly_payment, currency_symbol)])
        for c in cards:
            used = c.credit_limit - c.available_balance
            liab_data.append([c.bank_name + ' Card', 'Credit Card', fmt(used, currency_symbol), fmt(c.minimum_payment, currency_symbol)])
        total_liab = sum(l.outstanding_balance for l in loans) + sum(c.credit_limit - c.available_balance for c in cards)
        liab_data.append(['TOTAL LIABILITIES', '', fmt(total_liab, currency_symbol), ''])
        liab_t = Table(liab_data, colWidths=[65*mm, 30*mm, 44*mm, 35*mm])
        liab_t.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (-1,0),  C_RED),
            ('TEXTCOLOR',     (0,0), (-1,0),  colors.white),
            ('FONTNAME',      (0,0), (-1,0),  'Helvetica-Bold'),
            ('FONTNAME',      (0,-1), (-1,-1), 'Helvetica-Bold'),
            ('BACKGROUND',    (0,-1), (-1,-1), colors.HexColor('#fee2e2')),
            ('TEXTCOLOR',     (0,-1), (-1,-1), C_RED),
            ('FONTSIZE',      (0,0), (-1,-1), 8),
            ('ROWBACKGROUNDS',(0,1), (-1,-2), [C_SURFACE, C_BG]),
            ('TOPPADDING',    (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('LEFTPADDING',   (0,0), (-1,-1), 8),
            ('GRID',          (0,0), (-1,-1), 0.25, C_BORDER),
            ('ALIGN',         (2,0), (-1,-1), 'RIGHT'),
        ]))
        story.append(liab_t)
        story.append(Spacer(1, 4*mm))

    # ── GOALS ──
    active_goals = [g for g in goals if g.status == 'active']
    if active_goals:
        story += section_header('🎯 Goals Progress', S)
        goal_rows = []
        for g in active_goals:
            pct = min(100, g.current_amount / g.target_amount * 100) if g.target_amount > 0 else 0
            goal_rows.append(progress_bar_row(
                f'{g.icon} {g.name} — {fmt(g.current_amount, currency_symbol)} / {fmt(g.target_amount, currency_symbol)}',
                g.current_amount, g.target_amount, C_ACCENT, 80
            ))
        goal_t = Table(goal_rows, colWidths=[90*mm, 60*mm, 24*mm])
        goal_t.setStyle(TableStyle([
            ('TOPPADDING',    (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('LEFTPADDING',   (0,0), (-1,-1), 0),
            ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ]))
        story.append(goal_t)
        story.append(Spacer(1, 4*mm))

    # ── HEALTH SCORE DETAILS ──
    if health.get('reasons'):
        story += section_header('💡 Recommendations', S)
        for reason in health['reasons']:
            story.append(Paragraph(f'• {reason}', S['body']))
        story.append(Spacer(1, 4*mm))

    # ── FOOTER ──
    story.append(Spacer(1, 8*mm))
    story.append(HRFlowable(width='100%', thickness=0.5, color=C_BORDER))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        f'FinanceOS · {user_email} · Generated {datetime.now().strftime("%d %b %Y %H:%M")} · Confidential',
        S['footer']
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def _stat_cell(label, value, color):
    """Helper to build a stat card cell for the summary table."""
    return Table(
        [[Paragraph(label.upper(), ParagraphStyle('sl', fontName='Helvetica-Bold', fontSize=6.5, textColor=C_TEXT3, leading=9))],
         [Paragraph(f'<b>{value}</b>', ParagraphStyle('sv', fontName='Helvetica-Bold', fontSize=11, textColor=color, leading=15))]],
        colWidths=[50*mm]
    )